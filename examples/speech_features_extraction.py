# ===========================================================================
# Without PCA:
#   ncpu=1:  16s
#   ncpu=2:  9.82
#   ncpu=4:  5.9s
#   ncpu=8:  4.3
#   ncpu=12: 4.0
# ===========================================================================
from __future__ import print_function, division, absolute_import
import matplotlib
matplotlib.use('Agg')

import numpy as np
import shutil
import os
from odin import visual
from odin import fuel as F, utils
from collections import defaultdict

PCA = True
datapath = F.load_digit_wav()
output_path = utils.get_datasetpath(name='digit', override=True)
feat = F.SpeechProcessor(datapath, output_path, audio_ext='wav', sr_new=16000,
                         win=0.025, shift=0.01, nb_melfilters=40, nb_ceps=13,
                         get_delta=2, get_energy=True, get_phase=False,
                         get_spec=True, get_pitch=True, get_vad=2, get_qspec=False,
                         pitch_threshold=0.8, cqt_bins=96,
                         vad_smooth=3, vad_minlen=0.1,
                         pca=PCA, pca_whiten=False, center=True,
                         save_stats=True, substitute_nan=None,
                         dtype='float16', datatype='memmap',
                         ncache=0.12, ncpu=12)
with utils.UnitTimer():
    feat.run()
shutil.copy(os.path.join(datapath.path, 'README.md'),
            os.path.join(output_path, 'README.md'))
# ====== check the preprocessed dataset ====== #
ds = F.Dataset(output_path, read_only=True)
print('Output path:', output_path)
print(ds)

for n in ds.keys():
    if '_pca' in n:
        pca = ds[n]
        if pca.components_ is None:
            print(n, 'components is None !')
        elif np.any(np.isnan(pca.components_)):
            print(n, 'contains NaN !')
        else:
            print(n, ':', ' '.join(['%.2f' % i + '-' + '%.2f' % j
                for i, j in zip(pca.explained_variance_ratio_[:8],
                                pca.explained_variance_[:8])]))

for name, segs in ds['vadids'].iteritems():
    if len(segs) == 0:
        start, end = ds['indices'][name]
        vad = ds['vad'][start:end].tolist()
        print("NO vadids for", name, np.sum(vad), vad)

if PCA:
    for name, (start, end) in ds['indices'].iteritems():
        for vad_start, vad_end in ds['vadids'][name]:
            assert vad_end > vad_start
            assert not np.any(
                np.isnan(ds['spec_pca'].transform(ds['spec'][vad_start:vad_end], n_components=2)))

ds.archive()
print("Archive at:", ds.archive_path)
# ====== plot the processed files ====== #
figpath = os.path.join(utils.get_tempdir(), 'speech_features.pdf')
files = np.random.choice(ds['indices'].keys(), size=3, replace=False)
for f in files:
    with visual.figure(ncol=1, nrow=5, dpi=180,
                       show=False, tight_layout=True):
        start, end = ds['indices'][f]
        vad = ds['vad'][start:end]
        energy = ds['energy'][start:end][:, 0]
        spec = ds['spec'][start:end]
        mspec = ds['mspec'][start:end][:, :40]
        mfcc = ds['mfcc'][start:end][:, :13]
        visual.subplot(4, 1, 1); visual.plot(energy.ravel())
        visual.subplot(4, 1, 2)
        visual.plot_spectrogram(spec.T, vad=vad)
        visual.subplot(4, 1, 3)
        visual.plot_spectrogram(mspec.T, vad=vad)
        visual.subplot(4, 1, 4)
        visual.plot_spectrogram(mfcc.T, vad=vad)

# ====== Visual cluster ====== #
if PCA:
    from sklearn.manifold import TSNE
    feat = 'mspec'
    X = []; y = []
    feat_pca = ds[feat + '_pca']
    for f, (start, end) in ds['indices']:
        X.append(
            np.mean(
                feat_pca.transform(ds[feat][start:end]), axis=0, keepdims=True
        ))
        y.append(int(f[0]))
    X = np.concatenate(X, axis=0)
    y = np.asarray(y)
    X_ = TSNE(n_components=2).fit_transform(X)
    colors = visual.generate_random_colors(len(set(y)), seed=12082518)
    y = [colors[i] for i in y]
    legend = {c: str(i) for i, c in enumerate(colors)}
    with visual.figure(ncol=1, nrow=5):
        visual.plot_scatter(X[:, 0], X[:, 1], color=y, legend=legend)
    with visual.figure(ncol=1, nrow=5):
        visual.plot_scatter(X_[:, 0], X_[:, 1], color=y, legend=legend)

# ====== save all the figure ====== #
visual.plot_save(figpath, tight_plot=True)
print("Figure saved to:", figpath)
