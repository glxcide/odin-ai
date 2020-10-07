from __future__ import annotations

from typing import Optional

import numpy as np
import tensorflow as tf
from odin.bay.vi.autoencoder.variational_autoencoder import (
    LayerCreator, RandomVariable, VariationalAutoencoder, _iter_lists)
from odin.networks import SequentialNetwork
from odin.utils import as_tuple
from tensorflow.python import keras
from tensorflow.python.eager import context


class ImplicitRankMinimizer(keras.layers.Layer):
  """Implicit rank-minimization (Variational) Autoencoder

  The idea is to add a link-list of linear weights between the encoder and the
  bottleneck, since stochastic gradient descent going to force a low-rank solution
  on the latent codes.

  Parameters
  ----------
  units : int
      number of latent units
  n_layers : int, optional
      number of linear weights to be added, by default 3
  share_weights : bool, optional
      all layers share the same weight matrix, by default False

  References
  ---------
  Jing, L., Zbontar, J. & LeCun, Y. Implicit Rank-Minimizing Autoencoder.
    arXiv:2010.00679 [cs, stat] (2020).
  """

  def __init__(self,
               units: int,
               n_layers: int = 3,
               share_weights: bool = False,
               initializer: str = 'glorot_uniform',
               regularizer: Optional[str] = None,
               constraint: Optional[str] = None,
               activity_regularizer: Optional[str] = None,
               name: str = 'IRM'):
    super().__init__(activity_regularizer=activity_regularizer, name=name)
    self.units = int(units)
    self.n_layers = int(n_layers)
    self.share_weights = bool(share_weights)
    self.linear_weights = []
    self.initializer = as_tuple(keras.initializers.get(initializer), N=n_layers)
    self.regularizer = as_tuple(keras.regularizers.get(regularizer), N=n_layers)
    self.constraint = as_tuple(keras.constraints.get(constraint), N=n_layers)

  def build(self, input_shape) -> ImplicitRankMinimizer:
    super().build(input_shape)
    input_dim = input_shape[-1]
    last_w = None
    for i, (init, regu, cons) in enumerate(
        zip(self.initializer, self.regularizer, self.constraint)):
      shape = (input_dim, self.units)
      if self.share_weights and last_w is not None and last_w.shape == shape:
        w = last_w
      else:
        w = self.add_weight(name=f'W{i}',
                            shape=shape,
                            dtype=self.dtype,
                            initializer=init,
                            regularizer=regu,
                            constraint=cons,
                            trainable=True)
      last_w = w
      input_dim = w.shape[-1]
      self.linear_weights.append(w)
    return self

  def call(self, inputs, training=None, **kwargs):
    for kernel in self.linear_weights:
      rank = inputs.shape.rank
      if rank == 2 or rank is None:
        if isinstance(inputs, tf.sparse.SparseTensor):
          inputs = tf.sparse.sparse_dense_matmul(inputs, kernel)
        else:
          inputs = tf.matmul(inputs, kernel)
      # Broadcast kernel to inputs.
      else:
        shape = inputs.shape.as_list()
        inputs = tf.tensordot(inputs, kernel, [[rank - 1], [0]])
        # Reshape the output back to the original ndim of the input.
        if not context.executing_eagerly():
          output_shape = shape[:-1] + [kernel.shape[-1]]
          inputs.set_shape(output_shape)
    return inputs

  def __str__(self):
    return (f'<IRM units:{self.units} n_layers:{self.n_layers} '
            f'share_weights:{self.share_weights}>')


class irmVAE(VariationalAutoencoder):
  """Implicit rank-minimization (Variational) Autoencoder

  The idea is to add a link-list of linear weights between the encoder and the
  bottleneck, since stochastic gradient descent going to force a low-rank solution
  on the latent codes.

  Parameters
  ----------
  n_layers : int, optional
      number of linear weights to be added, by default 3
  share_weights : bool, optional
      all layers share the same weight matrix, by default False

  References
  ---------
  Jing, L., Zbontar, J. & LeCun, Y. Implicit Rank-Minimizing Autoencoder.
    arXiv:2010.00679 [cs, stat] (2020).
  """

  def __init__(self,
               latents: LayerCreator = RandomVariable(64,
                                                      'vdeterministic',
                                                      projection=True,
                                                      name='Latents'),
               n_layers: int = 3,
               share_weights: bool = False,
               **kwargs):
    super().__init__(latents=latents, **kwargs)
    for i, layer in enumerate(self.encoder):
      layer = SequentialNetwork(
          layers=[
              layer,
              ImplicitRankMinimizer(units=64,
                                    n_layers=n_layers,
                                    share_weights=share_weights,
                                    name='IRM')
          ],
          name=f'{layer.name}_irm',
      )
      self.encoder[i] = layer
