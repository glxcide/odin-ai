# ===========================================================================
# Using TIDIGITS dataset to predict gender (Boy, Girl, Woman, Man)
# ===========================================================================
# Saved WAV file format:
#     0) [train|test]
#     1) [m|w|b|g] (alias for man, women, boy, girl)
#     2) [age]
#     3) [dialectID]
#     4) [speakerID]
#     5) [production]
#     6) [digit_sequence]
#     => "train_g_08_17_as_a_4291815"
# ===========================================================================
from __future__ import print_function, division, absolute_import
import matplotlib
matplotlib.use('Agg')

import os
os.environ['ODIN'] = 'gpu,float32,seed=12082518'
import shutil
import pickle

import numpy as np
import tensorflow as tf

from sklearn.metrics import confusion_matrix, accuracy_score

from odin import backend as K, nnet as N, fuel as F, visual as V
from odin.stats import train_valid_test_split, freqcount
from odin import ml
from odin import training
from odin import preprocessing as pp
from odin.visual import print_dist, print_confusion, print_hist
from odin.utils import (get_logpath, get_modelpath, get_datasetpath,
                        Progbar, unique_labels, chain, get_formatted_datetime,
                        as_tuple_of_shape, stdio, ctext, ArgController)

# ===========================================================================
# Input arguments
# ===========================================================================
args = ArgController(
).add('path', 'path to preprocessed TIDIGITS dataset'
).add('-task', '0-gender,1-dialect,2-digit,3-spk', 0
).add('-nmix', "Number of GMM mixture", 256
).add('-tdim', "Dimension of t-matrix", 128
).add('--gmm', "Force re-run training GMM", False
).add('--stat', "Force re-extraction of centered statistics", False
).add('--tmat', "Force re-run training Tmatrix", False
).add('--ivec', "Force re-run extraction of i-vector", False
).add('--all', "Run all the system again, just a shortcut", False
).parse()
args.gmm |= args.all
args.stat |= args.all | args.gmm
args.tmat |= args.all | args.stat
args.ivec |= args.all | args.tmat
if args.task not in (0, 1, 2, 3):
  raise ValueError("Task must be: 0, 1, 2, 3")
# ===========================================================================
# path
# ===========================================================================
# path to preprocessed dataset
ds = F.Dataset(args.path, read_only=True)
# ====== general path ====== #
EXP_DIR = '/tmp/exp_digit'
if not os.path.exists(EXP_DIR):
  os.mkdir(EXP_DIR)
# ====== start logging ====== #
LOG_PATH = os.path.join(EXP_DIR,
                        'log_%s.txt' % get_formatted_datetime(only_number=True))
stdio(LOG_PATH)
print("Exp-dir:", ctext(EXP_DIR, 'cyan'))
print("Log path:", ctext(LOG_PATH, 'cyan'))
# ====== ivec path ====== #
GMM_PATH = os.path.join(EXP_DIR, 'gmm')
TMAT_PATH = os.path.join(EXP_DIR, 'tmat')
Z_PATH = (
    os.path.join(EXP_DIR, 'Z_train'),
    os.path.join(EXP_DIR, 'Z_test'))
F_PATH = (
    os.path.join(EXP_DIR, 'F_train'),
    os.path.join(EXP_DIR, 'F_test'))
I_PATH = (
    os.path.join(EXP_DIR, 'I_train'),
    os.path.join(EXP_DIR, 'I_test'))
L_PATH = ( # labels
    os.path.join(EXP_DIR, 'L_train'),
    os.path.join(EXP_DIR, 'L_test'))
# ===========================================================================
# Const
# ===========================================================================
FEAT = 'mspec'
indices = list(ds['indices'].items())
# ====== GMM trainign ====== #
NMIX = args.nmix
GMM_NITER = 10
GMM_DOWNSAMPLE = 4
GMM_STOCHASTIC = True
GMM_DTYPE = 'float64'
# ====== IVEC training ====== #
TV_DIM = args.tdim
TV_NITER = 10
TV_DTYPE = 'float64'
# ===========================================================================
# Helper
# ===========================================================================
def is_train(x):
  return x.split('_')[0] == 'train'

def extract_gender(x):
  return x.split('_')[1]

def extract_dialect(x):
  return x.split('_')[3]

def extract_spk(x):
  return x.split('_')[4]

def extract_digit(x):
  return x.split('_')[6]

if args.task == 0:
  fn_extract = extract_gender
elif args.task == 1:
  fn_extract = extract_dialect
elif args.task == 2:
  fn_extract = extract_digit
  indices = [(name, (start, end)) for name, (start, end) in indices
             if len(extract_digit(name)) == 1]
elif args.task == 3:
  fn_extract = extract_spk
fn_label, labels = unique_labels([i[0] for i in indices],
                                 key_func=fn_extract,
                                 return_labels=True)
print("Labels:", ctext(labels, 'cyan'))
# ===========================================================================
# Preparing data
# ===========================================================================
train_files = [] # (name, (start, end)) ...
test_files = []
for name, (start, end) in indices:
  if is_train(name):
    train_files.append((name, (start, end)))
  else:
    test_files.append((name, (start, end)))
# name for each dataset, useful for later
data_name = ['train', 'test']
print("#Train:", len(train_files))
print("#Test:", len(test_files))
# ===========================================================================
# GMM
# ===========================================================================
if not os.path.exists(GMM_PATH) or args.gmm:
  gmm = ml.GMM(nmix=NMIX, nmix_start=1,
               niter=GMM_NITER,
               dtype=GMM_DTYPE,
               allow_rollback=True,
               exit_on_error=True,
               batch_size_cpu='auto', batch_size_gpu='auto',
               downsample=GMM_DOWNSAMPLE,
               stochastic_downsample=GMM_STOCHASTIC,
               device='gpu',
               seed=5218, path=GMM_PATH)
  gmm.fit((ds[FEAT], train_files))
else:
  with open(GMM_PATH, 'rb') as f:
    gmm = pickle.load(f)
print(gmm)
# ===========================================================================
# Extract Zero and first order statistics
# ===========================================================================
stats = {}
y_true = {}
for name, files, z_path, f_path, l_path in zip(
        data_name,
        (train_files, test_files),
        Z_PATH, F_PATH, L_PATH):
  # extracting zeroth and first order statistics
  if not all(os.path.exists(i) for i in (z_path, f_path, l_path)) or\
      args.stat:
    print('========= Extracting statistics for: "%s" =========' % name)
    gmm.transform_to_disk(X=ds[FEAT], indices=files,
                          pathZ=z_path, pathF=f_path, name_path=l_path,
                          dtype='float32', device='cpu',
                          override=True)
  # load the statistics in MmapData
  y_true[name] = [fn_label(i) for i in np.genfromtxt(fname=l_path, dtype=str)]
  stats[name] = (F.MmapData(path=z_path, read_only=True),
                 F.MmapData(path=f_path, read_only=True))
# ===========================================================================
# Training T-matrix
# ===========================================================================
if not os.path.exists(TMAT_PATH) or args.tmat:
  tmat = ml.Tmatrix(tv_dim=TV_DIM, gmm=gmm,
                    niter=TV_NITER, dtype=TV_DTYPE,
                    batch_size_cpu='auto', batch_size_gpu='auto',
                    device='mix', ncpu=1, gpu_factor=3,
                    path=TMAT_PATH)
  tmat.fit(X=(stats['train'][0], # Z_train
              stats['train'][1])) # F_train
else:
  with open(TMAT_PATH, 'rb') as f:
    tmat = pickle.load(f)
print(tmat)
# ===========================================================================
# Extracting I-vectors
# ===========================================================================
ivecs = {}
for i_path, name in zip(I_PATH, data_name):
  if not os.path.exists(i_path) or args.ivec:
    print('========= Extracting ivecs for: "%s" =========' % name)
    z, f = stats[name]
    tmat.transform_to_disk(path=i_path,
                           Z=z, F=f,
                           name_path=None,
                           dtype='float32',
                           override=True)
  # load extracted ivec
  ivecs[name] = F.MmapData(i_path, read_only=True)
# ====== print the i-vectors ====== #
for name in data_name:
  print(ctext('i-vectors:', 'cyan'))
  print(ctext(' *', 'yellow'), ivecs[name])
  print(ctext('z-stats:', 'cyan'))
  print(ctext(' *', 'yellow'), stats[name][0])
  print(ctext('f-stats:', 'cyan'))
  print(ctext(' *', 'yellow'), stats[name][1])
  print(ctext('labels:', 'cyan'))
  print(ctext(' *', 'yellow'), len(y_true[name]))
# ===========================================================================
# Backend
# ===========================================================================
def filelist_2_feat(feat, flist):
  X = []
  y = []
  for name, (start, end) in flist:
    x = ds[feat][start:end]
    x = np.mean(x, axis=0, keepdims=True)
    X.append(x)
    y.append(fn_label(name))
  return np.concatenate(X, axis=0), np.array(y)

def evaluate_features(X_train, y_train,
                      X_test, y_test,
                      verbose, title):
  print(ctext("==== LogisticRegression: '%s'" % title, 'cyan'))
  model = ml.LogisticRegression(nb_classes=labels)
  model.fit(X_train, y_train)
  model.evaluate(X_test, y_test)
# ====== cosine scoring ====== #
print(ctext("==== '%s'" % "Ivec cosine-scoring", 'cyan'))
scorer = ml.Scorer(wccn=True, lda=True, method='cosine')
scorer.fit(X=ivecs['train'], y=y_true['train'])
scorer.evaluate(ivecs['test'], y_true['test'], labels=labels)
# ====== svm scoring ====== #
print(ctext("==== '%s'" % "Ivec SVM-scoring", 'cyan'))
scorer = ml.Scorer(wccn=True, lda=True, method='svm')
scorer.fit(X=ivecs['train'], y=y_true['train'])
scorer.evaluate(ivecs['test'], y_true['test'], labels=labels)
# ====== i-vec ====== #
evaluate_features(X_train=ivecs['train'], y_train=y_true['train'],
                  X_test=ivecs['test'], y_test=y_true['test'],
                  verbose=False, title="I-vectors")
# ====== zero-th ====== #
evaluate_features(X_train=stats['train'][0], y_train=y_true['train'],
                  X_test=stats['test'][0], y_test=y_true['test'],
                  verbose=False, title="Zero-th stats")
# ====== super-vector ====== #
evaluate_features(X_train=stats['train'][1], y_train=y_true['train'],
                  X_test=stats['test'][1], y_test=y_true['test'],
                  verbose=False, title="Super-vector")
# ====== bnf ====== #
X_train, y_train = filelist_2_feat('bnf', flist=train_files)
X_test, y_test = filelist_2_feat('bnf', flist=test_files)
evaluate_features(X_train, y_train,
                  X_test, y_test,
                  verbose=False, title="BNF")
# ====== mspec ====== #
X_train, y_train = filelist_2_feat('mspec', flist=train_files)
X_test, y_test = filelist_2_feat('mspec', flist=test_files)
evaluate_features(X_train, y_train,
                  X_test, y_test,
                  verbose=False, title="Mel-spectrogram")