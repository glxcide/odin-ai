from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import tensorflow as tf
from odin.bay.random_variable import RVmeta
from odin.bay.vi.autoencoder.beta_vae import betaVAE
from odin.bay.vi.autoencoder.factor_discriminator import FactorDiscriminator
from odin.bay.vi.autoencoder.variational_autoencoder import (DatasetV2,
                                                             OptimizerV2,
                                                             TensorTypes,
                                                             TrainStep, VAEStep)
from odin.bay.vi.utils import prepare_ssl_inputs
from odin.utils import as_tuple
from tensorflow.python import keras
from tensorflow_probability.python.distributions import Distribution
from typing_extensions import Literal


# ===========================================================================
# Helpers
# ===========================================================================
def _split_if_tensor(x):
  if tf.is_tensor(x):
    x1, x2 = tf.split(x, 2, axis=0)
  else:
    x1 = x
    x2 = x
  return x1, x2


def _split_inputs(inputs, mask, call_kw):
  r""" Split the data into 2 partitions for training the VAE and
  Discriminator """
  # split inputs into 2 mini-batches here
  if tf.is_tensor(inputs):
    x1, x2 = tf.split(inputs, 2, axis=0)
  else:
    inputs = [tf.split(x, 2, axis=0) for x in tf.nest.flatten(inputs)]
    x1 = [i[0] for i in inputs]
    x2 = [i[1] for i in inputs]
  # split the mask
  mask1 = None
  mask2 = None
  if mask is not None:
    if tf.is_tensor(mask):
      mask1, mask2 = tf.split(mask, 2, axis=0)
    else:
      mask = [tf.split(m, 2, axis=0) for m in tf.nest.flatten(mask)]
      mask1 = [i[0] for i in mask]
      mask2 = [i[1] for i in mask]
  # split the call_kw
  call_kw1 = {}
  call_kw2 = {}
  for k, v in call_kw.items():
    is_list = False
    if isinstance(v, collections.Sequence):
      v = [_split_if_tensor(i) for i in v]
      call_kw1[k] = [i[0] for i in v]
      call_kw2[k] = [i[1] for i in v]
    else:
      v1, v2 = _split_if_tensor(v)
      call_kw1[k] = v1
      call_kw2[k] = v2
  return (x1, mask1, call_kw1), (x2, mask2, call_kw2)


@dataclass
class FactorDiscriminatorStep(VAEStep):
  vae: factorVAE

  def call(self):
    px_z, qz_x = self.vae.last_outputs
    # if only inputs is provided without labels, error for ssl model,
    # need to flatten the list here.
    qz_xprime = self.vae.encode(self.inputs,
                                training=self.training,
                                mask=self.mask,
                                **self.call_kw)
    # discriminator loss
    dtc_loss = self.vae.dtc_loss(qz_x=qz_x,
                                 qz_xprime=qz_xprime,
                                 training=self.training)
    metrics = dict(dtc_loss=dtc_loss)
    ## applying the classifier loss,
    # if model is semi-supervised and the labels is given
    supervised_loss = 0.
    inputs = as_tuple(self.inputs)
    if (self.vae.is_semi_supervised() and len(inputs) > 1):
      labels = inputs[1:]
      supervised_loss = self.vae.supervised_loss(labels,
                                                 qz_x=qz_x,
                                                 mask=self.mask,
                                                 training=self.training)
      metrics['supv_loss'] = supervised_loss
    return dtc_loss + supervised_loss, metrics


# ===========================================================================
# Main factorVAE
# ===========================================================================
class factorVAE(betaVAE):
  r""" The default encoder and decoder configuration is the same as proposed
  in (Kim et. al. 2018).

  The training procedure of factorVAE is as follows:

  ```
  foreach iter:
    X = minibatch()
    X1, X2 = split(X, 2, axis=0)

    pX_Z, qz_x = vae(X1, training=True)
    loss = -vae.elbo(X1, pX_Z, qz_x, training=True)
    vae_optimizer.apply_gradients(loss, vae.parameters)

    qz_xprime = vae.encode(X2, training=True)
    dtc_loss = vae.dtc_loss(qz_x, qz_xprime, training=True)
    dis_optimizer.apply_gradients(dtc_loss, dis.parameters)
  ```

  Parameters
  ------------
  discriminator : a Dictionary or `keras.layers.Layer`.
    Keywords arguments for creating the `FactorDiscriminator`
  maximize_tc : a Boolean. If True, instead of minimize total correlation
    for more factorized latents, try to maximize the divergence.
  gamma : a Scalar. Weight for minimizing total correlation
  beta : a Scalar. Weight for minimizing Kl-divergence to the prior
  lamda : a Scalar. Weight for minimizing the discriminator loss

  Note
  ------
  You should use double the `batch_size` since the minibatch will be splitted
  into 2 partitions for `X` and `X_prime`.

  It is recommended to use the same optimizers configuration like in the
  paper: `Adam(learning_rate=1e-4, beta_1=0.9, beta_2=0.999)` for the VAE
  and `Adam(learning_rate=1e-4, beta_1=0.5, beta_2=0.9)` for the
  discriminator.

  Discriminator's Adam has learning rate `1e-4` for dSprites and `1e-5` for
  Shapes3D and other colored image datasets.

  Reference
  -----------
    Kim, H., Mnih, A., 2018. Disentangling by Factorising.
      arXiv:1802.05983 [cs, stat].
  """

  def __init__(self,
               discriminator_units: List[int] = [1000, 1000, 1000, 1000, 1000],
               activation: Union[str, Callable[[], Any]] = tf.nn.leaky_relu,
               batchnorm: bool = False,
               gamma: float = 1.0,
               beta: float = 1.0,
               lamda: float = 1.0,
               maximize_tc: bool = False,
               **kwargs):
    ss_strategy = kwargs.pop('ss_strategy', 'logsumexp')
    labels = kwargs.pop(
        'labels', RVmeta(1, 'bernoulli', projection=True, name="discriminator"))
    super().__init__(beta=beta, **kwargs)
    self.gamma = tf.convert_to_tensor(gamma, dtype=self.dtype, name='gamma')
    self.lamda = tf.convert_to_tensor(lamda, dtype=self.dtype, name='lamda')
    ## init discriminator
    self.discriminator = FactorDiscriminator(
        units=as_tuple(discriminator_units),
        activation=activation,
        batchnorm=batchnorm,
        ss_strategy=ss_strategy,
        observation=labels)
    # VAE and discriminator must be trained separated so we split their params here
    self.maximize_tc = bool(maximize_tc)
    ## For training
    # store class for training factor discriminator, this allow later
    # modification without re-writing the train_steps method
    self._is_pretraining = False

  def build(self, input_shape) -> factorVAE:
    super().build(input_shape)
    self.discriminator.build(self.latent_shape)
    # split the parameters
    self.disc_params = self.discriminator.trainable_variables
    exclude = set(id(p) for p in self.disc_params)
    self.vae_params = [
        p for p in self.trainable_variables if id(p) not in exclude
    ]
    return self

  @property
  def is_pretraining(self):
    return self._is_pretraining

  def pretrain(self):
    r""" Pretraining only train the VAE without the factor discriminator """
    self._is_pretraining = True
    return self

  def finetune(self):
    self._is_pretraining = False
    return self

  def elbo_components(self, inputs, training=None, mask=None):
    llk, kl = super().elbo_components(inputs, mask=mask, training=training)
    px_z, qz_x = self.last_outputs
    # by default, this support multiple latents by concatenating all latents
    if self.is_pretraining:
      tc = 0.
    else:
      tc = self.total_correlation(qz_x=qz_x, training=training)
    if self.maximize_tc:
      tc = -tc
    kl['tc'] = tc
    return llk, kl

  def total_correlation(self,
                        qz_x: Distribution,
                        training: Optional[bool] = None) -> tf.Tensor:
    return self.gamma * self.discriminator.total_correlation(qz_x,
                                                             training=training)

  def dtc_loss(self,
               qz_x: Distribution,
               qz_xprime: Optional[Distribution] = None,
               training: Optional[bool] = None) -> tf.Tensor:
    r""" Discrimination loss between real and permuted codes Algorithm (2) """
    return self.lamda * self.discriminator.dtc_loss(
        qz_x, qz_xprime=qz_xprime, training=training)

  def train_steps(self,
                  inputs: Union[TensorTypes, List[TensorTypes]],
                  training: bool = True,
                  mask: Optional[TensorTypes] = None,
                  call_kw: Dict[str, Any] = {}) -> TrainStep:
    r""" Facilitate multiple steps training for each iteration (similar to GAN)

    Example:
    ```
    vae = factorVAE()
    x = vae.sample_data()
    vae_step, discriminator_step = list(vae.train_steps(x))
    # optimizer VAE with total correlation loss
    with tf.GradientTape(watch_accessed_variables=False) as tape:
      tape.watch(vae_step.parameters)
      loss, metrics = vae_step()
      tape.gradient(loss, vae_step.parameters)
    # optimizer the discriminator
    with tf.GradientTape(watch_accessed_variables=False) as tape:
      tape.watch(discriminator_step.parameters)
      loss, metrics = discriminator_step()
      tape.gradient(loss, discriminator_step.parameters)
    ```
    """
    # split the data
    (x1, mask1, call_kw1), \
      (x2, mask2, call_kw2) = _split_inputs(inputs, mask, call_kw)
    # first step optimize VAE with total correlation loss
    step1 = VAEStep(vae=self,
                    inputs=x1,
                    training=training,
                    mask=mask1,
                    call_kw=call_kw1,
                    parameters=self.vae_params)
    yield step1
    # second step optimize the discriminator for discriminate permuted code
    # skip training Discriminator of pretraining
    if not self.is_pretraining:
      step2 = FactorDiscriminatorStep(vae=self,
                                      inputs=x2,
                                      training=training,
                                      mask=mask2,
                                      call_kw=call_kw2,
                                      parameters=self.disc_params)
      yield step2

  def fit(self,
          train: Union[TensorTypes, DatasetV2],
          valid: Optional[Union[TensorTypes, DatasetV2]] = None,
          optimizer: Tuple[OptimizerV2, OptimizerV2] = [
              tf.optimizers.Adam(learning_rate=1e-4, beta_1=0.9, beta_2=0.999),
              tf.optimizers.Adam(learning_rate=1e-4, beta_1=0.5, beta_2=0.9)
          ],
          **kwargs):
    r""" Override the original fit method of keras to provide simplified
    procedure with `VariationalAutoencoder.optimize` and
    `VariationalAutoencoder.train_steps` """
    assert isinstance(optimizer, (tuple, list)) and len(optimizer) == 2, \
      ("Two different optimizer must be provided, "
       "one for VAE, and one of FactorDiscriminator")
    return super().fit(train=train, valid=valid, optimizer=optimizer, **kwargs)

  def __str__(self):
    text = super().__str__()
    text += "\n Discriminator:\n  "
    text += "\n  ".join(str(self.discriminator).split('\n'))
    return text


# ===========================================================================
# Same as Factor VAE but with multi-task semi-supervised extension
# ===========================================================================
class ssfactorVAE(factorVAE):
  r""" Semi-supervised Factor VAE

  Note:
    The classifier won't be optimized during the training, with an unstable
    latent space.

    But if a VAE is pretrained, then, the extracted latents  are feed into
    the classifier for training, then it could reach > 90% accuracy easily.
  """

  def __init__(self,
               labels: RVmeta = RVmeta(10,
                                       'onehot',
                                       projection=True,
                                       name="Labels"),
               alpha: float = 10.,
               ss_strategy: Literal['sum', 'logsumexp', 'mean', 'max',
                                    'min'] = 'logsumexp',
               **kwargs):
    super().__init__(ss_strategy=ss_strategy, labels=labels, **kwargs)
    self.n_labels = self.discriminator.n_outputs
    self.alpha = tf.convert_to_tensor(alpha, dtype=self.dtype, name='alpha')

  def encode(self, inputs, training=None, mask=None, **kwargs):
    X, y, mask = prepare_ssl_inputs(inputs, mask=mask, n_unsupervised_inputs=1)
    return super().encode(X[0], training=training, mask=None, **kwargs)

  def classify(self,
               inputs: Union[TensorTypes, List[TensorTypes]],
               training: Optional[bool] = None) -> Distribution:
    qz_x = self.encode(inputs, training=training)
    if hasattr(self.discriminator, '_to_samples'):
      z = self.discriminator._to_samples(qz_x)
    else:
      z = qz_x
    y = self.discriminator(z, training=training)
    assert isinstance(y, Distribution), \
      f"Discriminator must return a Distribution, but returned: {y}"
    return y

  def supervised_loss(self,
                      labels: tf.Tensor,
                      qz_x: Distribution,
                      mask: Optional[TensorTypes] = None,
                      training: Optional[bool] = None) -> tf.Tensor:
    """The semi-supervised classifier loss, `mask` is given to indicate
    labelled examples (i.e. `mask=1`), and otherwise, unlabelled examples.
    """
    return self.alpha * self.discriminator.supervised_loss(
        labels=labels, qz_x=qz_x, mask=mask, training=training)

  @classmethod
  def is_semi_supervised(self) -> bool:
    return True


# ===========================================================================
# Separated latents for TC factorization
# ===========================================================================
class factor2VAE(factorVAE):
  r"""The same architecture as `factorVAE`, however, utilize two different
  latents `Z` for contents generalizability and `C` for disentangling of
  invariant factors."""

  def __init__(self,
               latents: RVmeta = RVmeta(5,
                                        'mvndiag',
                                        projection=True,
                                        name='Latents'),
               factors: RVmeta = RVmeta(5,
                                        'mvndiag',
                                        projection=True,
                                        name="Factors"),
               **kwargs):
    latents = tf.nest.flatten(latents)
    assert isinstance(factors, RVmeta), \
      "factors must be instance of RVmeta, but given: %s" % \
        str(type(factors))
    latents.append(factors)
    super().__init__(latents=latents,
                     latent_dim=int(np.prod(factors.event_shape)),
                     **kwargs)
    self.factors = factors

  def _elbo(self, inputs, pX_Z, qz_x, mask, training):
    llk, div = super(betaVAE, self)._elbo(
        inputs,
        pX_Z,
        qz_x,
        mask=mask,
        training=training,
    )
    # only use the assumed factors space for total correlation
    tc = self.total_correlation(qz_x[-1], apply_gamma=True, training=training)
    if self.maximize_tc:
      tc = -tc
    div[f'tc_{self.factors.name}'] = tc
    return llk, div


class ssfactor2VAE(ssfactorVAE, factor2VAE):
  r""" Combination of Semi-supervised VAE and Factor-2 VAE which leverages
  both labelled samples and the use of 2 latents space (1 for contents, and
  1 for factors)

  Example:
  ```
  from odin.fuel import MNIST
  from odin.bay.vi.autoencoder import SemiFactor2VAE

  # load the dataset
  ds = MNIST()
  train = ds.create_dataset(partition='train', inc_labels=0.3, batch_size=128)
  valid = ds.create_dataset(partition='valid', inc_labels=1.0, batch_size=128)

  # construction of SemiFactor2VAE for MNIST dataset
  vae = SemiFactor2VAE(encoder='mnist',
                       outputs=RVmeta((28, 28, 1), 'bern', name="Image"),
                       latents=RVmeta(10, 'mvndiag', projection=True, name='Latents'),
                       factors=RVmeta(10, 'mvndiag', projection=True, name='Factors'),
                       alpha=10.,
                       n_labels=10,
                       ss_strategy='logsumexp')
  vae.fit(
      train,
      valid=valid,
      valid_freq=500,
      compile_graph=True,
      epochs=-1,
      max_iter=8000,
  )
  ```
  """

  def __init__(self,
               latents=RVmeta(5, 'mvndiag', projection=True, name='Latents'),
               factors=RVmeta(5, 'mvndiag', projection=True, name='Factors'),
               **kwargs):
    super().__init__(latents=latents, factors=factors, **kwargs)
