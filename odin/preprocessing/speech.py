# -*- coding: utf-8 -*-
# ===========================================================================
# The waveform and spectrogram preprocess utilities is adapted from:
# [librosa](https://github.com/bmcfee/librosa)
# Copyright (c) 2016, librosa development team.
# Modified work Copyright 2016-2017 TrungNT
# ===========================================================================
from __future__ import print_function, division, absolute_import

import os
import six
import math
import copy
from collections import OrderedDict
import warnings

import numpy as np
import scipy.fftpack as fft
import scipy.signal

from odin.utils import pad_center

MAX_MEM_BLOCK = 2**8 * 2**10
SMALL_FLOAT = 1e-20

# ===========================================================================
# Predefined variables of speech datasets
# ===========================================================================
# ==================== Predefined datasets information ==================== #
nist15_cluster_lang = OrderedDict([
    ['ara', ['ara-arz', 'ara-acm', 'ara-apc', 'ara-ary', 'ara-arb']],
    ['zho', ['zho-yue', 'zho-cmn', 'zho-cdo', 'zho-wuu']],
    ['eng', ['eng-gbr', 'eng-usg', 'eng-sas']],
    ['fre', ['fre-waf', 'fre-hat']],
    ['qsl', ['qsl-pol', 'qsl-rus']],
    ['spa', ['spa-car', 'spa-eur', 'spa-lac', 'por-brz']]
])
nist15_lang_list = np.asarray([
    # Egyptian, Iraqi, Levantine, Maghrebi, Modern Standard
    'ara-arz', 'ara-acm', 'ara-apc', 'ara-ary', 'ara-arb',
    # Cantonese, Mandarin, Min Dong, Wu
    'zho-yue', 'zho-cmn', 'zho-cdo', 'zho-wuu',
    # British, American, South Asian (Indian)
    'eng-gbr', 'eng-usg', 'eng-sas',
    # West african, Haitian
    'fre-waf', 'fre-hat',
    # Polish, Russian
    'qsl-pol', 'qsl-rus',
    # Caribbean, European, Latin American, Brazilian
    'spa-car', 'spa-eur', 'spa-lac', 'por-brz'])


def nist15_label(label):
    '''
    Return
    ------
    lang_id : int
        idx in the list of 20 language, None if not found
    cluster_id : int
        idx in the list of 6 clusters, None if not found
    within_cluster_id : int
        idx in the list of each clusters, None if not found
    '''
    label = label.replace('spa-brz', 'por-brz')
    rval = [None, None, None]
    # lang_id
    if label not in nist15_lang_list:
        raise ValueError('Cannot found label:%s' % label)
    rval[0] = np.argmax(label == nist15_lang_list)
    # cluster_id
    for c, x in enumerate(nist15_cluster_lang.iteritems()):
        j = x[1]
        if label in j:
            rval[1] = c
            rval[2] = j.index(label)
    return rval

# ==================== Timit ==================== #
timit_61 = ['aa', 'ae', 'ah', 'ao', 'aw', 'ax', 'ax-h', 'axr', 'ay',
    'b', 'bcl', 'ch', 'd', 'dcl', 'dh', 'dx', 'eh', 'el', 'em', 'en',
    'eng', 'epi', 'er', 'ey', 'f', 'g', 'gcl', 'h#', 'hh', 'hv', 'ih',
    'ix', 'iy', 'jh', 'k', 'kcl', 'l', 'm', 'n', 'ng', 'nx', 'ow',
    'oy', 'p', 'pau', 'pcl', 'q', 'r', 's', 'sh', 't', 'tcl', 'th',
    'uh', 'uw', 'ux', 'v', 'w', 'y', 'z', 'zh']
timit_39 = ['aa', 'ae', 'ah', 'aw', 'ay', 'b', 'ch', 'd',
    'dh', 'dx', 'eh', 'er', 'ey', 'f', 'g', 'hh', 'ih', 'iy', 'jh', 'k',
    'l', 'm', 'n', 'ng', 'ow', 'oy', 'p', 'r', 's', 'sh', 'sil', 't',
    'th', 'uh', 'uw', 'v', 'w', 'y', 'z']
timit_map = {'ao': 'aa', 'ax': 'ah', 'ax-h': 'ah', 'axr': 'er',
    'hv': 'hh', 'ix': 'ih', 'el': 'l', 'em': 'm',
    'en': 'n', 'nx': 'n',
    'eng': 'ng', 'zh': 'sh', 'ux': 'uw',
    'pcl': 'sil', 'tcl': 'sil', 'kcl': 'sil', 'bcl': 'sil',
    'dcl': 'sil', 'gcl': 'sil', 'h#': 'sil', 'pau': 'sil', 'epi': 'sil'}


def timit_phonemes(phn, map39=False, blank=False):
    ''' Included blank '''
    if type(phn) not in (list, tuple, np.ndarray):
        phn = [phn]
    if map39:
        timit = timit_39
        timit_map = timit_map
        l = 39
    else:
        timit = timit_61
        timit_map = {}
        l = 61
    # ====== return phonemes ====== #
    rphn = []
    for p in phn:
        if p not in timit_map and p not in timit:
            if blank: rphn.append(l)
        else:
            rphn.append(timit.index(timit_map[p]) if p in timit_map else timit.index(p))
    return rphn


# ===========================================================================
# Audio helper
# ===========================================================================
def read(f, pcm=False, remove_dc_offset=True):
    '''
    Return
    ------
        waveform (ndarray: [samples;channel]), sample rate (int)
    '''
    if pcm or (isinstance(f, str) and
               any(i in f for i in ['pcm', 'PCM'])):
        s, fs = (np.memmap(f, dtype=np.int16, mode='r'), None)
    else:
        from soundfile import read
        s, fs = read(f)
    s = s.astype(np.float32)
    if remove_dc_offset:
        s -= np.mean(s, 0)
    return s, fs


def pre_emphasis(s, coeff=0.97):
    """Pre-emphasis of an audio signal.
    Parameters
    ----------
    s: np.ndarray
        the input vector of signal to pre emphasize
    coeff: float (0, 1)
        coefficience that defines the pre-emphasis filter.
    """
    if s.ndim == 1:
        return np.append(s[0], s[1:] - coeff * s[:-1])
    else:
        return s - np.c_[s[:, :1], s[:, :-1]] * coeff


def est_audio_length(fpath, fs=8000, bitdepth=16):
    """ Estimate audio length in second """
    if not os.path.exists(fpath):
        raise Exception('File at path:%s does not exist' % fpath)
    return os.path.getsize(fpath) / (bitdepth / 8) / 8000


def resample(s, fs_orig, fs_new, axis=0, best_algorithm=True):
    '''
    '''
    fs_orig = int(fs_orig)
    fs_new = int(fs_new)
    if fs_orig != fs_new:
        import resampy
        s = resampy.resample(s, sr_orig=fs_orig, sr_new=fs_new, axis=axis,
                             filter='kaiser_best' if best_algorithm else 'kaiser_fast')
    return s


def save(f, s, fs, subtype=None):
    '''
    Return
    ------
        waveform (ndarray), sample rate (int)
    '''
    from soundfile import write
    return write(f, s, fs, subtype=subtype)


# ===========================================================================
# Spectrogram manipulation
# ===========================================================================
def vad_energy(log_energy,
               distrib_nb=3,
               nb_train_it=8,
               flooring=0.0001, ceiling=1.0,
               alpha=2):
    from sidekit.mixture import Mixture
    # center and normalize the energy
    log_energy = (log_energy - np.mean(log_energy)) / np.std(log_energy)

    # Initialize a Mixture with 2 or 3 distributions
    world = Mixture()
    # set the covariance of each component to 1.0 and the mean to mu + meanIncrement
    world.cst = np.ones(distrib_nb) / (np.pi / 2.0)
    world.det = np.ones(distrib_nb)
    world.mu = -2 + 4.0 * np.arange(distrib_nb) / (distrib_nb - 1)
    world.mu = world.mu[:, np.newaxis]
    world.invcov = np.ones((distrib_nb, 1))
    # set equal weights for each component
    world.w = np.ones(distrib_nb) / distrib_nb
    world.cov_var_ctl = copy.deepcopy(world.invcov)

    # Initialize the accumulator
    accum = copy.deepcopy(world)

    # Perform nbTrainIt iterations of EM
    for it in range(nb_train_it):
        accum._reset()
        # E-step
        world._expectation(accum, log_energy)
        # M-step
        world._maximization(accum, ceiling, flooring)

    # Compute threshold
    threshold = world.mu.max() - alpha * np.sqrt(1.0 / world.invcov[world.mu.argmax(), 0])

    # Apply frame selection with the current threshold
    label = log_energy > threshold
    return label, threshold


def istft(stft_matrix, hop_length=None, win_length=None, window=None,
          center=True, dtype=np.float32):
    """
    Inverse short-time Fourier transform (ISTFT).

    Converts a complex-valued spectrogram `stft_matrix` to time-series `y`
    by minimizing the mean squared error between `stft_matrix` and STFT of
    `y` as described in [1]_.

    In general, window function, hop length and other parameters should be same
    as in stft, which mostly leads to perfect reconstruction of a signal from
    unmodified `stft_matrix`.

    .. [1] D. W. Griffin and J. S. Lim,
        "Signal estimation from modified short-time Fourier transform,"
        IEEE Trans. ASSP, vol.32, no.2, pp.236–243, Apr. 1984.

    Parameters
    ----------
    stft_matrix : np.ndarray [shape=(1 + n_fft/2, t)]
        STFT matrix from `stft`

    hop_length  : int > 0 [scalar]
        Number of frames between STFT columns.
        If unspecified, defaults to `win_length / 4`.

    win_length  : int <= n_fft = 2 * (stft_matrix.shape[0] - 1)
        When reconstructing the time series, each frame is windowed
        and each sample is normalized by the sum of squared window
        according to the `window` function (see below).

        If unspecified, defaults to `n_fft`.

    window      : None, function, np.ndarray [shape=(n_fft,)]
        - None (default): use an asymmetric Hann window
        - a window function, such as `scipy.signal.hanning`
        - a user-specified window vector of length `n_fft`

    center      : boolean
        - If `True`, `D` is assumed to have centered frames.
        - If `False`, `D` is assumed to have left-aligned frames.

    dtype       : numeric type
        Real numeric type for `y`.  Default is 32-bit float.

    Returns
    -------
    y : np.ndarray [shape=(n,)]
        time domain signal reconstructed from `stft_matrix`

    Raises
    ------
    ValueError
        If `window` is supplied as a vector of length `n_fft`

    See Also
    --------
    stft : Short-time Fourier Transform

    """

    n_fft = 2 * (stft_matrix.shape[0] - 1)

    # By default, use the entire frame
    if win_length is None:
        win_length = n_fft

    # Set the default hop, if it's not already specified
    if hop_length is None:
        hop_length = int(win_length / 4)

    if window is None:
        # Default is an asymmetric Hann window.
        ifft_window = scipy.signal.hann(win_length, sym=False)

    elif six.callable(window):
        # User supplied a windowing function
        ifft_window = window(win_length)

    else:
        # User supplied a window vector.
        # Make it into an array
        ifft_window = np.asarray(window)

        # Verify that the shape matches
        if ifft_window.size != n_fft:
            raise ValueError('Size mismatch between n_fft and window size')

    # Pad out to match n_fft
    ifft_window = pad_center(ifft_window, n_fft)

    n_frames = stft_matrix.shape[1]
    expected_signal_len = n_fft + hop_length * (n_frames - 1)
    y = np.zeros(expected_signal_len, dtype=dtype)
    ifft_window_sum = np.zeros(expected_signal_len, dtype=dtype)
    ifft_window_square = ifft_window * ifft_window

    for i in range(n_frames):
        sample = i * hop_length
        spec = stft_matrix[:, i].flatten()
        spec = np.concatenate((spec.conj(), spec[-2:0:-1]), 0)
        ytmp = ifft_window * fft.ifft(spec).real

        y[sample:(sample + n_fft)] = y[sample:(sample + n_fft)] + ytmp
        ifft_window_sum[sample:(sample + n_fft)] += ifft_window_square

    # Normalize by sum of squared window
    approx_nonzero_indices = ifft_window_sum > SMALL_FLOAT
    y[approx_nonzero_indices] /= ifft_window_sum[approx_nonzero_indices]

    if center:
        y = y[int(n_fft // 2):-int(n_fft // 2)]

    return y


def compute_delta(data, width=9, order=1, axis=-1, trim=True):
    r'''Compute delta features: local estimate of the derivative
    of the input data along the selected axis.

    Parameters
    ----------
    data      : np.ndarray
        the input data matrix (eg, spectrogram), shape=(d, t)
    width     : int >= 3, odd [scalar]
        Number of frames over which to compute the delta feature
    order     : int > 0 [scalar]
        the order of the difference operator.
        1 for first derivative, 2 for second, etc.
    axis      : int [scalar]
        the axis along which to compute deltas.
        Default is -1 (columns).
    trim      : bool
        set to `True` to trim the output matrix to the original size.

    Returns
    -------
    delta_data   : list(np.ndarray) [shape=(d, t) or (d, t + window)]
        delta matrix of `data`.
        return list of deltas

    Examples
    --------
    Compute MFCC deltas, delta-deltas
    >>> mfcc = mfcc(y=y, sr=sr)
    >>> mfcc_delta1, mfcc_delta2 = compute_delta(mfcc, 2)
    '''

    data = np.atleast_1d(data)

    if width < 3 or np.mod(width, 2) != 1:
        raise ValueError('width must be an odd integer >= 3')

    if order <= 0 or not isinstance(order, int):
        raise ValueError('order must be a positive integer')

    half_length = 1 + int(width // 2)
    window = np.arange(half_length - 1., -half_length, -1.)

    # Normalize the window so we're scale-invariant
    window /= np.sum(np.abs(window)**2)

    # Pad out the data by repeating the border values (delta=0)
    padding = [(0, 0)] * data.ndim
    width = int(width)
    padding[axis] = (width, width)
    delta_x = np.pad(data, padding, mode='edge')

    all_deltas = []
    for _ in range(order):
        delta_x = scipy.signal.lfilter(window, 1, delta_x, axis=axis)
        all_deltas.append(delta_x)

    # Cut back to the original shape of the input data
    if trim:
        _ = []
        for delta_x in all_deltas:
            idx = [slice(None)] * delta_x.ndim
            idx[axis] = slice(- half_length - data.shape[axis], - half_length)
            delta_x = delta_x[idx]
            _.append(delta_x.astype('float32'))
        all_deltas = _

    return all_deltas


def speech_features(s, sr, win, shift,
                    nb_melfilters, nb_ceps, nb_delta,
                    get_spec, get_mspec, get_mfcc, get_pitch, get_vad, get_energy,
                    pitch_threshold=0.8, fmin=64, fmax=None,
                    sr_new=None, preemphasis=0.97):
    """ Automatically extract multiple acoustic representation of
    speech features

    Parameters
    ----------
    s: np.ndarray
        raw signal
    sr: int
        sample rate
    win: float
        window length in millisecond
    shift: float
        hop length between windows, in millisecond
    nb_filters:
    nb_ceps:
    nb_delta:
    get_spec:
    get_mspec:
    get_mfcc:
    get_pitch:
    get_vad:
    pitch_threshold:
    fmin: int
    fmax: int or None
    sr_new: int or None
        new sample rate
    preemphasis:

    return spec(X, sum, sum2),
               mspec(X, sum, sum2),
               mfcc(X, sum, sum2),
               vad_idx
    """
    if np.prod(s.shape) == np.max(s.shape):
        s = s.ravel()
    elif s.ndim >= 2:
        raise Exception('Speech Feature Extraction only accept 1-D signal')
    import librosa
    # ====== resample if necessary ====== #
    if sr_new is not None and int(sr_new) != int(sr):
        s = resample(s, sr, sr_new, axis=0, best_algorithm=False)
        sr = sr_new
    if fmax is None:
        fmax = sr // 2
    if fmin is None or fmin < 0 or fmin >= fmax:
        fmin = 0
    n_fft = int(win * sr)
    hop_length = int(shift * sr)
    # preemphais
    s = pre_emphasis(s, coeff=preemphasis)
    # ====== 0: extract VAD and energy ====== #
    if get_energy or get_vad:
        frames = librosa.util.frame(s, frame_length=n_fft, hop_length=hop_length)
        log_energy = np.log((frames**2).sum(axis=0)).astype('float32')[None, :]
        if get_vad:
            distribNb, nbTrainIt = 8, 12
            if isinstance(get_vad, (tuple, list)):
                distribNb, nbTrainIt = get_vad
            # vad_idx = sidekit.frontend.vad.vad_snr(s, threshold,
            # fs=fs, shift=shift, nwin=int(fs * win)).astype('int8')
            vad = vad_energy(log_energy.ravel(), distrib_nb=distribNb,
                             nb_train_it=nbTrainIt)[0].astype('int8')
    if not get_energy:
        log_energy = None
    if not get_vad:
        vad = None
    # ====== 1: extract STFT and Spectrogram ====== #
    stft = librosa.stft(s, n_fft=n_fft, hop_length=hop_length,
                        center=False) # no padding for center
    S, D = librosa.magphase(stft)
    # ====== 2: extract pitch features ====== #
    if get_pitch:
        # we don't care about pitch magnitude
        pitch_freq, _ = librosa.piptrack(
            y=None, sr=sr, S=S, n_fft=n_fft, hop_length=hop_length,
            fmin=fmin, fmax=fmax, threshold=pitch_threshold)
        pitch_freq = pitch_freq.astype('float32')
    else:
        pitch_freq = None
    # ====== 3: extract power spectrogram ====== #
    S = S**2
    if get_spec:
        powerspectrogram = librosa.logamplitude(
            S, ref_power=1.0, amin=1e-10, top_db=80.0).astype('float32')
    else:
        powerspectrogram = None
    # ====== 4: extract log-mel filter bank ====== #
    if get_mspec or get_mfcc:
        melspectrogram = librosa.feature.melspectrogram(
            y=None, sr=sr, S=S, n_fft=n_fft, hop_length=hop_length,
            n_mels=nb_melfilters, fmin=fmin, fmax=fmax, htk=False)
        melspectrogram = librosa.logamplitude(
            melspectrogram, ref_power=1.0, amin=1e-10, top_db=80.0).astype('float32')
        if get_mfcc:
            mfcc = librosa.feature.mfcc(
                y=None, sr=sr, S=melspectrogram, n_mfcc=nb_ceps).astype('float32')
    if not get_mspec:
        melspectrogram = None
    if not get_mfcc:
        mfcc = None
    # ====== 5: compute delta ====== #
    if nb_delta and nb_delta > 0:
        if pitch_freq is not None:
            pitch_freq = np.concatenate(
                [pitch_freq] + compute_delta(pitch_freq, order=nb_delta),
                axis=0)
        if powerspectrogram is not None:
            powerspectrogram = np.concatenate(
                [powerspectrogram] + compute_delta(powerspectrogram, order=nb_delta),
                axis=0)
        if melspectrogram is not None:
            melspectrogram = np.concatenate(
                [melspectrogram] + compute_delta(melspectrogram, order=nb_delta),
                axis=0)
        if mfcc is not None:
            mfcc = np.concatenate(
                [mfcc] + compute_delta(mfcc, order=nb_delta),
                axis=0)
        if log_energy is not None:
            log_energy = np.concatenate(
                [log_energy] + compute_delta(log_energy, order=nb_delta),
                axis=0)
    return {'spec': powerspectrogram.T, 'mspec': melspectrogram.T, 'mfcc': mfcc.T,
            'vad': vad, 'pitch': pitch_freq.T, 'energy': log_energy.T}
