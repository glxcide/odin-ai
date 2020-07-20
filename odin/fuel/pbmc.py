import base64
import os
from urllib.request import urlretrieve

import numpy as np
import tensorflow as tf
from scipy import sparse

from odin.fuel._image_base import _partition
from odin.utils.crypto import md5_checksum


class PBMC(object):
  _URL = {
      '5k':
          b'aHR0cHM6Ly9haS1kYXRhc2V0cy5zMy5hbWF6b25hd3MuY29tL3BibWM1ay5ucHo=\n',
      '10k':
          b'aHR0cHM6Ly9haS1kYXRhc2V0cy5zMy5hbWF6b25hd3MuY29tL3BibWMxMGsubnB6\n'
  }

  def __init__(self, dataset='5k', path="~/tensorflow_datasets/pbmc"):
    path = os.path.abspath(os.path.expanduser(path))
    self.dsname = dataset
    if not os.path.exists(path):
      os.makedirs(path)
    url = str(base64.decodebytes(PBMC._URL[str(dataset).lower().strip()]),
              'utf-8')
    name = os.path.basename(url)
    filename = os.path.join(path, name)
    urlretrieve(url,
                filename=filename,
                reporthook=lambda blocknum, bs, size: None)
    ### load the data
    data = np.load(filename, allow_pickle=True)
    self.x = data['x'].tolist().todense().astype(np.float32)
    self.y = data['y'].tolist().todense().astype(np.float32)
    assert md5_checksum(self.x) == data['xmd5'].tolist(), \
      "MD5 for transcriptomic data mismatch"
    assert md5_checksum(self.y) == data['ymd5'].tolist(), \
      "MD5 for proteomic data mismatch"
    self.xvar = data['xvar']
    self.yvar = data['yvar']
    self.pairs = data['pairs']
    ### split train, valid, test data
    rand = np.random.RandomState(seed=1)
    n = self.x.shape[0]
    ids = rand.permutation(n)
    self.train_ids = ids[:int(0.85 * n)]
    self.valid_ids = ids[int(0.85 * n):int(0.9 * n)]
    self.test_ids = ids[int(0.9 * n):]

  @property
  def var_names(self):
    return self.xvar

  @property
  def name(self):
    return f"pbmc{self.dsname}"

  @property
  def n_labels(self):
    return self.y.shape[1]

  @property
  def labels(self):
    return self.yvar

  @property
  def shape(self):
    return tuple(self.x.shape[1:])

  @property
  def is_binary(self):
    return False

  def create_dataset(self,
                     batch_size=64,
                     drop_remainder=False,
                     shuffle=1000,
                     prefetch=tf.data.experimental.AUTOTUNE,
                     cache='',
                     parallel=None,
                     partition='train',
                     inc_labels=False,
                     seed=1) -> tf.data.Dataset:
    ids = _partition(partition,
                     train=self.train_ids,
                     valid=self.valid_ids,
                     test=self.test_ids)
    x = self.x[ids]
    y = self.y[ids]
    gen = tf.random.experimental.Generator.from_seed(seed=seed)

    def _process(*data):
      if inc_labels:
        if 0. < inc_labels < 1.:  # semi-supervised mask
          mask = gen.uniform(shape=(1,)) < inc_labels
          return dict(inputs=data, mask=mask)
      return data

    ds = tf.data.Dataset.from_tensor_slices(x)
    if inc_labels > 0.:
      ds = tf.data.Dataset.zip((ds, tf.data.Dataset.from_tensor_slices(y)))
    ds = ds.map(_process, parallel)
    if cache is not None:
      ds = ds.cache(str(cache))
    # shuffle must be called after cache
    if shuffle is not None and shuffle > 0:
      ds = ds.shuffle(int(shuffle))
    ds = ds.batch(batch_size, drop_remainder)
    if prefetch is not None:
      ds = ds.prefetch(prefetch)
    return ds