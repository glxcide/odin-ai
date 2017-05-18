# -*- coding: utf-8 -*-
# ===========================================================================
# General toolkits for Signal processing in pure python, numpy and scipy
# The code is collected and modified from following library:
# * https://gist.github.com/kastnerkyle/179d6e9a88202ab0a2fe
#       License: BSD 3-clause
#       Authors: Kyle Kastner
#       Harvest, Cheaptrick, D4C, WORLD routines based on MATLAB code from M. Morise
#       http://ml.cs.yamanashi.ac.jp/world/english/
#       MGC code based on r9y9 (Ryusshichi Yamamoto) MelGeneralizedCepstrums.jl
#       Pieces also adapted from SPTK
# * https://github.com/librosa/librosa
#       License: https://github.com/librosa/librosa/blob/master/LICENSE.md
# * http://www-lium.univ-lemans.fr/sidekit/
#       Authors: Anthony Larcher & Kong Aik Lee & Sylvain Meignier
#       License: GNU Library or Lesser General Public License (LGPL)
# ===========================================================================
from __future__ import print_function, division, absolute_import

import six
import warnings
from numbers import Number
from six import string_types

import numpy as np
import scipy as sp
from scipy import linalg, fftpack
from numpy.lib.stride_tricks import as_strided
try:
    from odin.utils import cache_memory, cache_disk
except ImportError:
    def cache_memory(func):
        return func

    def cache_disk(func):
        return func


# Constrain STFT block sizes to 512 KB
MAX_MEM_BLOCK = 2**8 * 2**11


# ===========================================================================
#
# ===========================================================================
def hz2mel(frequencies):
    """Convert Hz to Mels
    Original code: librosa

    Examples
    --------
    >>> hz2mel(60)
    array([ 0.9])
    >>> hz2mel([110, 220, 440])
    array([ 1.65,  3.3 ,  6.6 ])

    Parameters
    ----------
    frequencies   : np.ndarray [shape=(n,)] , float
        scalar or array of frequencies

    Returns
    -------
    mels        : np.ndarray [shape=(n,)]
        input frequencies in Mels

    See Also
    --------
    mel_to_hz
    """
    frequencies = np.atleast_1d(frequencies)
    # Fill in the linear part
    f_min = 0.0
    f_sp = 200.0 / 3
    mels = (frequencies - f_min) / f_sp
    # Fill in the log-scale part
    min_log_hz = 1000.0                         # beginning of log region (Hz)
    min_log_mel = (min_log_hz - f_min) / f_sp   # same (Mels)
    logstep = np.log(6.4) / 27.0                # step size for log region

    log_t = (frequencies >= min_log_hz)
    mels[log_t] = min_log_mel + np.log(frequencies[log_t] / min_log_hz) / logstep
    return mels


def mel2hz(mels):
    """Convert mel bin numbers to frequencies
    Original code: librosa

    Examples
    --------
    >>> mel2hz(3)
    array([ 200.])
    >>> mel2hz([1,2,3,4,5])
    array([  66.667,  133.333,  200.   ,  266.667,  333.333])

    Parameters
    ----------
    mels          : np.ndarray [shape=(n,)], float
        mel bins to convert

    Returns
    -------
    frequencies   : np.ndarray [shape=(n,)]
        input mels in Hz

    See Also
    --------
    hz_to_mel
    """

    mels = np.atleast_1d(mels)

    # Fill in the linear scale
    f_min = 0.0
    f_sp = 200.0 / 3
    freqs = f_min + f_sp * mels

    # And now the nonlinear scale
    min_log_hz = 1000.0                         # beginning of log region (Hz)
    min_log_mel = (min_log_hz - f_min) / f_sp   # same (Mels)
    logstep = np.log(6.4) / 27.0                # step size for log region
    log_t = (mels >= min_log_mel)

    freqs[log_t] = min_log_hz * np.exp(logstep * (mels[log_t] - min_log_mel))
    return freqs


def power2db(S, ref=1.0, amin=1e-10, top_db=80.0):
    """Convert a power spectrogram (amplitude/magnitude squared)
    to decibel (dB) units (using logarithm)

    This computes the scaling ``10 * log10(S / ref)`` in a numerically
    stable way.

    Original code: librosa

    Parameters
    ----------
    S : np.ndarray
        input power

    ref : scalar or callable
        If scalar, the amplitude `abs(S)` is scaled relative to `ref`:
        `10 * log10(S / ref)`.
        Zeros in the output correspond to positions where `S == ref`.
        If callable, the reference value is computed as `ref(S)`.

    amin : float > 0 [scalar]
        minimum threshold for `abs(S)` and `ref`

    top_db : float >= 0 [scalar]
        threshold the output at `top_db` below the peak:
        ``max(10 * log10(S)) - top_db``

    Returns
    -------
    S_db   : np.ndarray
        ``S_db ~= 10 * log10(S) - 10 * log10(ref)``
    """
    if amin <= 0:
        raise ValueError('amin must be strictly positive')
    magnitude = np.abs(S)
    if callable(ref):
        # User supplied a function to calculate reference power
        ref_value = ref(magnitude)
    else:
        ref_value = np.abs(ref)
    log_spec = 10.0 * np.log10(np.maximum(amin, magnitude))
    log_spec -= 10.0 * np.log10(np.maximum(amin, ref_value))
    # clip top db
    if top_db is not None:
        if top_db < 0:
            raise ValueError('top_db must be non-negative')
        log_spec = np.maximum(log_spec, log_spec.max() - top_db)
    return log_spec


@cache_memory
def dct_filters(n_filters, n_input):
    """Discrete cosine transform (DCT type-III) basis.

    .. [1] http://en.wikipedia.org/wiki/Discrete_cosine_transform

    Original code: librosa

    Parameters
    ----------
    n_filters : int > 0 [scalar]
        number of output components (DCT filters)

    n_input : int > 0 [scalar]
        number of input components (frequency bins)

    Returns
    -------
    dct_basis: np.ndarray [shape=(n_filters, n_input)]
        DCT (type-III) basis vectors [1]_

    Notes
    -----
    This function caches at level 10.

    Examples
    --------
    >>> n_fft = 2048
    >>> dct_filters = librosa.filters.dct(13, 1 + n_fft // 2)
    >>> dct_filters
    array([[ 0.031,  0.031, ...,  0.031,  0.031],
           [ 0.044,  0.044, ..., -0.044, -0.044],
           ...,
           [ 0.044,  0.044, ..., -0.044, -0.044],
           [ 0.044,  0.044, ...,  0.044,  0.044]])

    >>> import matplotlib.pyplot as plt
    >>> plt.figure()
    >>> librosa.display.specshow(dct_filters, x_axis='linear')
    >>> plt.ylabel('DCT function')
    >>> plt.title('DCT filter bank')
    >>> plt.colorbar()
    >>> plt.tight_layout()
    """
    basis = np.empty((n_filters, n_input))
    basis[0, :] = 1.0 / np.sqrt(n_input)

    samples = np.arange(1, 2 * n_input, 2) * np.pi / (2.0 * n_input)

    for i in range(1, n_filters):
        basis[i, :] = np.cos(i * samples) * np.sqrt(2.0 / n_input)
    return basis


@cache_memory
def mel_filters(sr, n_fft, n_mels=128, fmin=0.0, fmax=None):
    """Create a Filterbank matrix to combine FFT bins into Mel-frequency bins
    Original code: librosa

    Parameters
    ----------
    sr        : number > 0 [scalar]
        sampling rate of the incoming signal

    n_fft     : int > 0 [scalar]
        number of FFT components

    n_mels    : int > 0 [scalar]
        number of Mel bands to generate

    fmin      : float >= 0 [scalar]
        lowest frequency (in Hz)

    fmax      : float >= 0 [scalar]
        highest frequency (in Hz).
        If `None`, use `fmax = sr / 2.0`

    Returns
    -------
    M         : np.ndarray [shape=(n_mels, 1 + n_fft/2)]
        Mel transform matrix

    Examples
    --------
    >>> melfb = mel_filters(22050, 2048)
    >>> melfb
    array([[ 0.   ,  0.016, ...,  0.   ,  0.   ],
           [ 0.   ,  0.   , ...,  0.   ,  0.   ],
           ...,
           [ 0.   ,  0.   , ...,  0.   ,  0.   ],
           [ 0.   ,  0.   , ...,  0.   ,  0.   ]])
    """
    if fmax is None:
        fmax = float(sr) / 2
    # Initialize the weights
    n_mels = int(n_mels)
    weights = np.zeros((n_mels, int(1 + n_fft // 2)))

    # Center freqs of each FFT bin
    fftfreqs = np.linspace(0, float(sr) / 2, int(1 + n_fft // 2),
                           endpoint=True)

    # 'Center freqs' of mel bands - uniformly spaced between limits
    min_mel = hz2mel(fmin)
    max_mel = hz2mel(fmax)
    mel_f = mel2hz(mels=np.linspace(min_mel, max_mel, n_mels + 2))

    fdiff = np.diff(mel_f)
    ramps = np.subtract.outer(mel_f, fftfreqs)

    for i in range(n_mels):
        # lower and upper slopes for all bins
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]

        # .. then intersect them with each other and zero
        weights[i] = np.maximum(0, np.minimum(lower, upper))

    # Slaney-style mel is scaled to be approx constant energy per channel
    enorm = 2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels])
    weights *= enorm[:, np.newaxis]

    # Only check weights if f_mel[0] is positive
    if not np.all((mel_f[:-2] == 0) | (weights.max(axis=1) > 0)):
        # This means we have an empty channel somewhere
        print('[WARNING] Empty filters detected in mel frequency basis. '
              'Some channels will produce empty responses. '
              'Try increasing your sampling rate (and fmax) or '
              'reducing n_mels.')
    return weights


@cache_memory
def get_window(window, Nx, fftbins=True):
    ''' Cached version of scipy.signal.get_window '''
    if six.callable(window):
        return window(Nx)
    elif (isinstance(window, (six.string_types, tuple)) or
          np.isscalar(window)):
        return sp.signal.get_window(window, Nx, fftbins=fftbins)
    elif isinstance(window, (np.ndarray, list)):
        if len(window) == Nx:
            return np.asarray(window)
        raise ValueError('Window size mismatch: '
                         '{:d} != {:d}'.format(len(window), Nx))
    else:
        raise ValueError('Invalid window specification: {}'.format(window))


# ===========================================================================
# Array utils
# ===========================================================================
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


def smooth(x, win=11, window='hanning'):
    """
    Paramaters
    ----------
    x: 1-D vector
        input signal.
    win: int
        length of window for smoothing, the longer the window, the more details
        are reduced for smoothing.
    window: 'flat', 'hanning', 'hamming', 'bartlett', 'blackman'
        window function, 'flat' for moving average.

    Return
    ------
    y: smoothed vector

    """
    if win < 3:
        return x
    if window not in ['flat', 'hanning', 'hamming', 'bartlett', 'blackman']:
        raise ValueError("Window is on of 'flat', 'hanning', 'hamming', 'bartlett', 'blackman'")
    s = np.concatenate([2 * x[0] - x[win - 1::-1],
                        x,
                        2 * x[-1] - x[-1:-win:-1]], axis=0)
    # moving average
    if window == 'flat':
        w = np.ones(win, 'd')
    # windowing
    else:
        w = eval('np.' + window + '(win)')
    y = np.convolve(w / w.sum(), s, mode='same')
    return y[win:-win + 1]


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
        delta_x = sp.signal.lfilter(window, 1, delta_x, axis=axis)
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


VAD_MODE_STRICT = 1.2
VAD_MODE_STANDARD = 2.
VAD_MODE_SENSITIVE = 2.4
__current_vad_mode = VAD_MODE_STANDARD # alpha for vad energy


def set_vad_mode(mode):
    """
    Paramters
    ---------
    mode: float
        a number from 1.0 to 2.4, the higher the number, the more
        sensitive it is to any high-energy segments.
    """
    if isinstance(mode, Number):
        global __current_vad_mode
        mode = min(max(mode, 1.), 2.4)
        __current_vad_mode = float(mode)


def vad_energy(log_energy, distrib_nb=2, nb_train_it=24):
    from sklearn.mixture import GaussianMixture
    # center and normalize the energy
    log_energy = (log_energy - np.mean(log_energy)) / np.std(log_energy)
    if log_energy.ndim == 1:
        log_energy = log_energy[:, np.newaxis]
    # create mixture model: diag, spherical
    world = GaussianMixture(
        n_components=distrib_nb, covariance_type='diag',
        init_params='kmeans', max_iter=nb_train_it,
        weights_init=np.ones(distrib_nb) / distrib_nb,
        means_init=(-2 + 4.0 * np.arange(distrib_nb) / (distrib_nb - 1))[:, np.newaxis],
        precisions_init=np.ones((distrib_nb, 1)),
    )
    try:
        world.fit(log_energy)
    except (ValueError, IndexError): # index error because of float32 cumsum
        if distrib_nb - 1 >= 2:
            return vad_energy(log_energy, distrib_nb=distrib_nb - 1,
                              nb_train_it=nb_train_it)
        return np.zeros(shape=(log_energy.shape[0],)), 0
    # Compute threshold
    threshold = world.means_.max() - \
        __current_vad_mode * np.sqrt(1.0 / world.precisions_[world.means_.argmax(), 0])
    # Apply frame selection with the current threshold
    label = log_energy.ravel() > threshold
    return label, threshold


@cache_memory('__strict__')
def pad_center(data, size, axis=-1, **kwargs):
    '''Wrapper for numpy.pad to automatically center an array prior to padding.
    This is analogous to `str.center()`

    Parameters
    ----------
    data : numpy.ndarray
        Vector to be padded and centered

    size : int >= len(data) [scalar]
        Length to pad `data`

    axis : int
        Axis along which to pad and center the data

    kwargs : additional keyword arguments
      arguments passed to `numpy.pad()`

    Returns
    -------
    data_padded : numpy.ndarray
        `data` centered and padded to length `size` along the
        specified axis

    Raises
    ------
    ParameterError
        If `size < data.shape[axis]`

    See Also
    --------
    numpy.pad
    '''
    kwargs.setdefault('mode', 'constant')

    n = data.shape[axis]

    lpad = int((size - n) // 2)

    lengths = [(0, 0)] * data.ndim
    lengths[axis] = (lpad, int(size - n - lpad))

    if lpad < 0:
        raise ValueError(('Target size ({:d}) must be '
                          'at least input size ({:d})').format(size, n))
    return np.pad(data, lengths, **kwargs)


def pad_sequences(sequences, maxlen=None, dtype='int32',
                  padding='pre', truncating='pre', value=0.,
                  transformer=None):
    """Pads each sequence to the same length:
    the length of the longest sequence.

    If maxlen is provided, any sequence longer
    than maxlen is truncated to maxlen.
    Truncation happens off either the beginning (default) or
    the end of the sequence.

    Supports post-padding and pre-padding (default).

    Parameters
    ----------
    sequences: list
        a list that contains a list of object
    maxlen: int
        maximum length of each individual sequence
    dtype: np.dtype
        desire data type of output array
    padding: 'pre' or 'post'
        pad either before or after each sequence.
    truncating: 'pre' or 'post'
        remove values from sequences larger than maxlen either
        in the beginning or in the end of the sequence
    value: object
        padding object
    transformer: callable
        a function transform each element in sequence into desire value
        (e.g. a dictionary)

    Returns
    -------
    numpy array with dimensions (number_of_sequences, maxlen)
    """
    # ====== check valid input ====== #
    if truncating not in ('pre', 'post'):
        raise ValueError('truncating must be "pre" or "post", given value is %s'
                         % truncating)
    if padding not in ('pre', 'post'):
        raise ValueError('padding must be "pre" or "post", given value is %s'
                         % padding)
    if transformer is None:
        transformer = lambda x: x
    if not callable(transformer):
        raise ValueError('transformer must be callable, but given value is %s' %
                         type(transformer))
    # ====== processing ====== #
    if maxlen is None:
        maxlen = int(max(len(s) for s in sequences))
    nb_samples = len(sequences)
    value = np.cast[dtype](value)
    X = np.full(shape=(nb_samples, maxlen), fill_value=value, dtype=dtype)
    for idx, s in enumerate(sequences):
        s = [transformer(_) for _ in s]
        if len(s) == 0: continue # empty list
        # check truncating
        if len(s) >= maxlen:
            slice_ = slice(None, None)
            s = s[-maxlen:] if truncating == 'pre' else s[:maxlen]
        # check padding
        elif len(s) < maxlen:
            slice_ = slice(-len(s), None) if padding == 'pre' else slice(None, len(s))
        # assign value
        X[idx, slice_] = np.asarray(s, dtype=dtype)
    return X


def segment_axis(a, frame_length=2048, hop_length=512, axis=0,
                 end='cut', endvalue=0, endmode='post'):
    """Generate a new array that chops the given array along the given axis
    into overlapping frames.

    This method has been implemented by Anne Archibald,
    as part of the talk box toolkit
    example::

        segment_axis(arange(10), 4, 2)
        array([[0, 1, 2, 3],
           ( [2, 3, 4, 5],
             [4, 5, 6, 7],
             [6, 7, 8, 9]])

    Parameters
    ----------
    a: numpy.ndarray
        the array to segment
    frame_length: int
        the length of each frame
    hop_length: int
        the number of array elements by which the frames should overlap
    axis: int, None
        the axis to operate on; if None, act on the flattened array
    end: 'cut', 'wrap', 'pad'
        what to do with the last frame, if the array is not evenly
            divisible into pieces. Options are:
            - 'cut'   Simply discard the extra values
            - 'wrap'  Copy values from the beginning of the array
            - 'pad'   Pad with a constant value
    endvalue: int
        the value to use for end='pad'
    endmode: 'pre', 'post'
        if "pre", padding or wrapping at the beginning of the array.
        if "post", padding or wrapping at the ending of the array.

    Return
    ------
    a ndarray

    The array is not copied unless necessary (either because it is unevenly
    strided and being flattened or because end is set to 'pad' or 'wrap').

    Note
    ----
    Modified work and error fixing Copyright (c) TrungNT

    """
    if axis is None:
        a = np.ravel(a) # may copy
        axis = 0

    length = a.shape[axis]
    overlap = frame_length - hop_length

    if overlap >= frame_length:
        raise ValueError("frames cannot overlap by more than 100%")
    if overlap < 0 or frame_length <= 0:
        raise ValueError("overlap must be nonnegative and length must" +
                         "be positive")

    if length < frame_length or (length - frame_length) % (frame_length - overlap):
        if length > frame_length:
            roundup = frame_length + (1 + (length - frame_length) // (frame_length - overlap)) * (frame_length - overlap)
            rounddown = frame_length + ((length - frame_length) // (frame_length - overlap)) * (frame_length - overlap)
        else:
            roundup = frame_length
            rounddown = 0
        assert rounddown < length < roundup
        assert roundup == rounddown + (frame_length - overlap) \
        or (roundup == frame_length and rounddown == 0)
        a = a.swapaxes(-1, axis)

        if end == 'cut':
            a = a[..., :rounddown]
        elif end in ['pad', 'wrap']: # copying will be necessary
            s = list(a.shape)
            s[-1] = roundup
            b = np.empty(s, dtype=a.dtype)
            # pre-padding
            if endmode == 'pre':
                b[..., :length] = a
                if end == 'pad':
                    b[..., length:] = endvalue
                elif end == 'wrap':
                    b[..., length:] = a[..., :roundup - length]
            # post-padding
            elif endmode == 'post':
                b[..., -length:] = a
                if end == 'pad':
                    b[..., :(roundup - length)] = endvalue
                elif end == 'wrap':
                    b[..., :(roundup - length)] = a[..., :roundup - length]
            a = b
        a = a.swapaxes(-1, axis)
        length = a.shape[0] # update length

    if length == 0:
        raise ValueError("Not enough data points to segment array " +
                "in 'cut' mode; try 'pad' or 'wrap'")
    assert length >= frame_length
    assert (length - frame_length) % (frame_length - overlap) == 0
    n = 1 + (length - frame_length) // (frame_length - overlap)
    s = a.strides[axis]
    newshape = a.shape[:axis] + (n, frame_length) + a.shape[axis + 1:]
    newstrides = a.strides[:axis] + ((frame_length - overlap) * s, s) + a.strides[axis + 1:]

    try:
        return np.ndarray.__new__(np.ndarray, strides=newstrides,
                                  shape=newshape, buffer=a, dtype=a.dtype)
    except TypeError:
        a = a.copy()
        # Shape doesn't change but strides does
        newstrides = a.strides[:axis] + ((frame_length - overlap) * s, s) \
        + a.strides[axis + 1:]
        return np.ndarray.__new__(np.ndarray, strides=newstrides,
                                  shape=newshape, buffer=a, dtype=a.dtype)


# ===========================================================================
# Fourier transform
# ===========================================================================
def stft(y, n_fft=256, hop_length=None, window='hann',
         center=False, preemphasis=None, energy=False):
    """Short-time Fourier transform (STFT)

    Returns a complex-valued matrix D such that
        `np.abs(D[f, t])` is the magnitude of frequency bin `f`
        at frame `t`

        `np.angle(D[f, t])` is the phase of frequency bin `f`
        at frame `t`

    Parameters
    ----------
    y : np.ndarray [shape=(n,)], real-valued
        the input signal (audio time series)

    n_fft : int > 0 [scalar]
        FFT window size

    hop_length : int > 0 [scalar]
        number audio of frames between STFT columns.
        If unspecified, defaults `win_length / 4`.

    win_length  : int <= n_fft [scalar]
        Each frame of audio is windowed by `window()`.
        The window will be of length `win_length` and then padded
        with zeros to match `n_fft`.

        If unspecified, defaults to ``win_length = n_fft``.

    window : string, tuple, number, function, or np.ndarray [shape=(n_fft,)]
        - a window specification (string, tuple, or number);
          see `scipy.signal.get_window`
        - a window function, such as `scipy.signal.hanning`
        - a vector or array of length `n_fft`

        .. see also:: `filters.get_window`

    center      : boolean
        - If `True`, the signal `y` is padded so that frame
          `D[:, t]` is centered at `y[t * hop_length]`.
        - If `False`, then `D[:, t]` begins at `y[t * hop_length]`

    preemphasis: 0.97
    energy: False, True or 'log'

    Returns
    -------
    D : np.ndarray [shape=(t, 1 + n_fft/2), dtype=complex64]
        STFT matrix
    """
    # if n_fft is None:
    # n_fft = 2 ** int(np.ceil(np.log2(win_length)))
    n_fft = int(n_fft)
    # Set the default hop, if it's not already specified
    if hop_length is None:
        hop_length = n_fft // 4
    hop_length = int(hop_length)
    fft_window = get_window(window, n_fft, fftbins=True)
    # Reshape so that the window can be broadcast
    fft_window = fft_window.reshape((-1, 1))
    # pre-emphasis
    if isinstance(preemphasis, Number) and 0. < preemphasis < 1.:
        y = pre_emphasis(y, coeff=preemphasis)
    # Pad the time series so that frames are centered
    if center:
        y = np.pad(y, int(n_fft // 2), mode='reflect')
    # Window the time series.
    y_frames = segment_axis(y, frame_length=n_fft, hop_length=hop_length,
        end='cut').T
    # calculate frames energy
    if energy:
        log_energy = (y_frames**2).sum(axis=0)
        log_energy = np.where(log_energy == 0., np.finfo(np.float32).eps,
                              log_energy)
        if isinstance(energy, string_types) and energy.lower() == 'log':
            log_energy = np.log(log_energy)
    # Pre-allocate the STFT matrix
    stft_matrix = np.empty((int(1 + n_fft // 2), y_frames.shape[1]),
                           dtype=np.complex64, order='F')
    # how many columns can we fit within MAX_MEM_BLOCK?
    n_columns = int(MAX_MEM_BLOCK / (stft_matrix.shape[0] *
                                     stft_matrix.itemsize))
    for bl_s in range(0, stft_matrix.shape[1], n_columns):
        bl_t = min(bl_s + n_columns, stft_matrix.shape[1])
        # RFFT and Conjugate here to match phase from DPWE code
        stft_matrix[:, bl_s:bl_t] = fftpack.fft(
            fft_window * y_frames[:, bl_s:bl_t], axis=0
        )[:stft_matrix.shape[0]].conj()
    # return in form (t, d)
    if energy:
        return stft_matrix.T, log_energy.astype('float32')
    return stft_matrix.T


def istft(stft_matrix, hop_length=None, window='hann', center=False):
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

    window      : string, tuple, number, function, np.ndarray [shape=(n_fft,)]
        - a window specification (string, tuple, or number);
          see `scipy.signal.get_window`
        - a window function, such as `scipy.signal.hanning`
        - a user-specified window vector of length `n_fft`

        .. see also:: `filters.get_window`

    center      : boolean
        - If `True`, `D` is assumed to have centered frames.
        - If `False`, `D` is assumed to have left-aligned frames.

    dtype       : numeric type
        Real numeric type for `y`.  Default is 32-bit float.

    Returns
    -------
    y : np.ndarray [shape=(n,), dtype=float32]
        time domain signal reconstructed from `stft_matrix`
    """
    n_fft = 2 * (stft_matrix.shape[1] - 1)
    # Set the default hop, if it's not already specified
    if hop_length is None:
        hop_length = n_fft // 4
    hop_length = int(hop_length)
    ifft_window = get_window(window, n_fft, fftbins=True)
    # Pad out to match n_fft
    ifft_window = pad_center(ifft_window, n_fft, '__cache__')

    n_frames = stft_matrix.shape[0]
    expected_signal_len = n_fft + hop_length * (n_frames - 1)
    y = np.zeros(expected_signal_len, dtype=np.float32)
    ifft_window_sum = np.zeros(expected_signal_len, dtype=np.float32)
    ifft_window_square = ifft_window * ifft_window

    for i in range(n_frames):
        sample = i * hop_length
        spec = stft_matrix[i, :].flatten()
        spec = np.concatenate((spec.conj(), spec[-2:0:-1]), 0)
        ytmp = ifft_window * fftpack.ifft(spec).real

        y[sample:(sample + n_fft)] = y[sample:(sample + n_fft)] + ytmp
        ifft_window_sum[sample:(sample + n_fft)] += ifft_window_square
    # Normalize by sum of squared window
    approx_nonzero_indices = ifft_window_sum > np.finfo(np.float32).tiny
    y[approx_nonzero_indices] /= ifft_window_sum[approx_nonzero_indices]
    if center:
        y = y[int(n_fft // 2):-int(n_fft // 2)]
    return y


def spectra(sr, y=None, S=None,
            n_fft=256, hop_length=None, window='hann',
            nb_melfilters=None, nb_ceps=None, fmin=64, fmax=None,
            top_db=80.0, power=2.0, log=True):
    """Compute spectra information from STFT matrix or a power spectrogram,
    The extracted spectra include:
    * log-power spectrogram
    * mel-scaled spectrogram.
    * MFCC (cepstrum analysis)

    If a spectrogram input `S` is provided, then it is mapped directly onto
    the mel basis `mel_f` by `mel_f.dot(S)`.

    If a time-series input `y, sr` is provided, then its magnitude spectrogram
    `S` is first computed, and then mapped onto the mel scale by
    `mel_f.dot(S**power)`.  By default, `power=2` operates on a power spectrum.

    Parameters
    ----------
    y : np.ndarray [shape=(n,)] or None
        audio time-series

    sr : number > 0 [scalar]
        sampling rate of `y`

    S : np.ndarray [shape=(d, t)]
        spectrogram

    n_fft : int > 0 [scalar]
        length of the FFT window

    hop_length : int > 0 [scalar]
        number of samples between successive frames.
        See `librosa.core.stft`

    power : float > 0 [scalar]
        Exponent for the magnitude melspectrogram.
        e.g., 1 for energy, 2 for power, etc.

    Returns
    -------
    S : np.ndarray [shape=(n_mels, t)]
        Mel spectrogram

    """
    # ====== STFT matrix not specified ====== #
    if S is None:
        S = stft(y, n_fft=n_fft, hop_length=hop_length, window=window,
                 center=False, preemphasis=0.97, energy=False)
    n_fft = int(2 * (S.shape[1] - 1))
    # ====== check arguments ====== #
    power = int(power)
    # check fmax
    if sr is None and fmax is None:
        fmax = 4000
    else:
        fmax = sr // 2 if fmax is None else int(fmax)
    # check fmin
    fmin = int(fmin)
    if fmin >= fmax:
        raise ValueError("fmin must < fmax.")
    # ====== extract the basic spectrogram ====== #
    if 'complex' in str(S.dtype): # STFT
        spec = np.abs(S)
    if power > 1:
        spec = np.power(spec, power)
    spec = spec.astype('float32')
    # ====== extrct mel-filter-bands features ====== #
    mel_spec = None
    if nb_melfilters is not None or nb_ceps is not None:
        mel_basis = mel_filters(sr, n_fft=n_fft,
            n_mels=24 if nb_melfilters is None else int(nb_melfilters),
            fmin=fmin, fmax=fmax)
        # transpose to (nb_samples; nb_mels)
        mel_spec = np.dot(mel_basis, spec.T)
        mel_spec = mel_spec.astype('float32')
    if mel_spec is not None:
        mel_spec = power2db(mel_spec, top_db=top_db)
    # ====== extract cepstrum features ====== #
    mfcc = None
    if nb_ceps is not None:
        nb_ceps = int(nb_ceps) + 1
        dct_basis = dct_filters(nb_ceps, mel_spec.shape[0])
        mfcc = np.dot(dct_basis, mel_spec).astype('float32')[1:, :]
    # ====== return result ====== #
    results = {}
    results['spec'] = power2db(spec) if log else spec
    if nb_melfilters is not None:
        results['mspec'] = mel_spec.T
    if nb_ceps is not None:
        results['mfcc'] = mfcc.T
    return results