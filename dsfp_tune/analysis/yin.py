"""YIN fundamental-frequency (f0) estimator, with the difference function computed by FFT.

Reference: A. de Cheveigné & H. Kawahara (2002), "YIN, a fundamental frequency
estimator for speech and music", JASA 111(4).

How the FFT fits in
-------------------
YIN is a *time-domain* method: it looks for the lag ``tau`` at which a frame best
matches a delayed copy of itself. The expensive part is the difference function

    d(tau) = sum_{j=0}^{W-1} (x[j] - x[j+tau])^2

which costs O(W * tau_max) if computed directly. Expanding the square gives

    d(tau) = E0 + E(tau) - 2 r(tau)

    E0      = sum_{j<W} x[j]^2                 (energy of the reference window)
    E(tau)  = sum_{j<W} x[j+tau]^2             (energy of the shifted window; cumulative sum)
    r(tau)  = sum_{j<W} x[j] * x[j+tau]        (cross-correlation; FFT)

and the cross-correlation is computed with FFTs in O(N log N):

    r = IFFT( conj(FFT(x[:W])) * FFT(x) )

The frame is NOT windowed (no Hann etc.) and is zero-padded to ``n_fft >= W + tau_max``
so the circular correlation has no wrap-around. The result is identical (to float
precision) to the direct formula, just much faster.

Usage
-----
Real-time style, one frame at a time::

    yin = Yin(sample_rate=44100, f_min=70, f_max=1000)
    frame = samples[start : start + yin.frame_length]
    est = yin.detect(frame)          # -> PitchEstimate(f0, aperiodicity, voiced)

Offline, a whole signal::

    track = yin_track(samples, sample_rate=44100, hop_length=256)
    track.times, track.f0, track.voiced
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from dsfp_tune.native import lib as _native

__all__ = [
    "PitchEstimate",
    "PitchTrack",
    "Yin",
    "YinStream",
    "yin_track",
    "difference_function",
    "difference_function_direct",
    "cumulative_mean_normalized_difference",
    "absolute_threshold",
    "parabolic_interpolation",
]


# ---------------------------------------------------------------------------
# The algorithm, step by step (pure functions; easy to test and plot)
# ---------------------------------------------------------------------------


def difference_function(x: np.ndarray, window: int, tau_max: int, n_fft: int | None = None) -> np.ndarray:
    """Step 1: YIN difference function ``d(tau)`` for ``tau = 0 .. tau_max``, via FFT.

    Parameters
    ----------
    x:
        Time-domain frame with at least ``window + tau_max`` samples (extra samples are ignored).
    window:
        Integration window ``W`` (number of products summed per lag).
    tau_max:
        Largest lag to evaluate, in samples.
    n_fft:
        FFT size; must be ``>= window + tau_max``. Defaults to the next power of two.
    """
    length = window + tau_max
    if x.shape[-1] < length:
        raise ValueError(f"frame has {x.shape[-1]} samples, need window + tau_max = {length}")
    if n_fft is None:
        n_fft = 1 << (length - 1).bit_length()
    elif n_fft < length:
        raise ValueError(f"n_fft={n_fft} is too small; need >= {length} to avoid wrap-around")

    x = np.asarray(x[:length], dtype=np.float64)

    # r(tau): cross-correlation of the reference window x[:W] with the whole frame.
    ref_spec = np.fft.rfft(x[:window], n_fft)
    frame_spec = np.fft.rfft(x, n_fft)
    r = np.fft.irfft(np.conj(ref_spec) * frame_spec, n_fft)[: tau_max + 1]

    # E0 and E(tau) from a running sum of squares.
    csum = np.concatenate(([0.0], np.cumsum(x * x)))
    e0 = csum[window]
    e_tau = csum[window : window + tau_max + 1] - csum[: tau_max + 1]

    d = e0 + e_tau - 2.0 * r
    d[0] = 0.0
    # FFT round-off can make tiny values slightly negative; d is a sum of squares.
    return np.maximum(d, 0.0)


def difference_function_direct(x: np.ndarray, window: int, tau_max: int) -> np.ndarray:
    """Step 1, computed literally from the definition. Slow; used to check the FFT version."""
    x = np.asarray(x, dtype=np.float64)
    ref = x[:window]
    return np.array([np.sum((ref - x[tau : tau + window]) ** 2) for tau in range(tau_max + 1)])


def cumulative_mean_normalized_difference(d: np.ndarray) -> np.ndarray:
    """Step 2: ``d'(tau) = d(tau) / ((1/tau) * sum_{j=1}^{tau} d(j))``, with ``d'(0) = 1``.

    Values near 0 mean "this lag repeats the signal well"; values around 1 or above mean
    "no better than average". This removes the trivial dip at ``tau = 0``.
    """
    cmnd = np.ones_like(d, dtype=np.float64)
    running = np.cumsum(d[1:])
    taus = np.arange(1, len(d))
    with np.errstate(divide="ignore", invalid="ignore"):
        vals = d[1:] * taus / running
    # A silent frame gives 0/0; treat it as completely aperiodic.
    cmnd[1:] = np.where(running > 0, vals, 1.0)
    return cmnd


def absolute_threshold(cmnd: np.ndarray, tau_min: int, threshold: float) -> tuple[int, bool]:
    """Step 3: choose the period (integer lag).

    Take the *first* lag ``>= tau_min`` where ``d'`` drops below ``threshold``, then follow
    the curve down to the bottom of that dip. Choosing the first dip (not the deepest)
    avoids picking 2x or 3x the period, i.e. octave-too-low errors.

    Returns ``(tau, found)``. If no lag is below the threshold, returns the global minimum
    in ``[tau_min, tau_max]`` with ``found=False`` (the frame is probably unvoiced).
    """
    search = cmnd[tau_min:]
    below = np.flatnonzero(search < threshold)
    if below.size == 0:
        return tau_min + int(np.argmin(search)), False

    tau = tau_min + int(below[0])
    last = len(cmnd) - 1
    while tau < last and cmnd[tau + 1] < cmnd[tau]:
        tau += 1
    return tau, True


def parabolic_interpolation(y: np.ndarray, i: int) -> tuple[float, float]:
    """Step 4: refine a minimum at integer index ``i`` by fitting a parabola to ``y[i-1:i+2]``.

    Returns ``(x_min, y_min)``. At the array edges, or if the points are collinear,
    returns the integer position unchanged.
    """
    if i <= 0 or i >= len(y) - 1:
        return float(i), float(y[i])
    a, b, c = y[i - 1], y[i], y[i + 1]
    denom = a - 2.0 * b + c
    if denom <= 0:  # flat or not a minimum: no refinement
        return float(i), float(b)
    shift = 0.5 * (a - c) / denom  # always within (-0.5, 0.5) for a true local minimum
    return i + shift, float(b - 0.25 * (a - c) * shift)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PitchEstimate:
    """Result for one frame.

    f0:
        Estimated fundamental frequency in Hz, or ``nan`` if the frame is unvoiced.
    aperiodicity:
        ``d'`` at the chosen lag: ~0 = clearly periodic, ~1 = noise.
    voiced:
        True if a dip was found below the threshold and the frame is above the silence gate.
    """

    f0: float
    aperiodicity: float
    voiced: bool

    @property
    def confidence(self) -> float:
        """``1 - aperiodicity``, clipped to [0, 1]."""
        return float(min(1.0, max(0.0, 1.0 - self.aperiodicity)))


class Yin:
    """Frame-by-frame YIN pitch detector.

    Parameters
    ----------
    sample_rate:
        Sample rate of the audio, in Hz.
    f_min, f_max:
        Pitch search range in Hz. ``f_min`` sets the longest period searched and therefore
        the frame length (latency): lower ``f_min`` means longer frames.
    window:
        Integration window ``W`` in samples. Defaults to ``tau_max`` (one longest period).
    threshold:
        Absolute threshold on ``d'`` (step 3). 0.1-0.15 is typical; lower is stricter.
    silence_db:
        Frames whose RMS is below this level (dBFS) are reported unvoiced without analysis.
        ``None`` disables the gate. (YIN itself is level-independent, so without this, quiet
        hum or bleed can be reported as pitched.)
    """

    def __init__(
        self,
        sample_rate: int,
        f_min: float = 70.0,
        f_max: float = 1000.0,
        window: int | None = None,
        threshold: float = 0.1,
        silence_db: float | None = -60.0,
    ) -> None:
        if not 0 < f_min < f_max:
            raise ValueError("need 0 < f_min < f_max")
        if f_max > sample_rate / 4:
            raise ValueError(f"f_max={f_max} too high for sample_rate={sample_rate} (max sr/4)")
        if not 0 < threshold < 1:
            raise ValueError("threshold must be in (0, 1)")

        self.sample_rate = int(sample_rate)
        self.f_min = float(f_min)
        self.f_max = float(f_max)
        self.threshold = float(threshold)
        self.silence_db = silence_db

        self.tau_min = max(2, int(math.floor(sample_rate / f_max)))
        self.tau_max = int(math.ceil(sample_rate / f_min))
        self.window = int(window) if window is not None else self.tau_max
        if self.window < 1:
            raise ValueError("window must be positive")

        #: Samples needed per call to :meth:`detect` (this is the analysis latency).
        self.frame_length = self.window + self.tau_max
        self.n_fft = 1 << (self.frame_length - 1).bit_length()
        self._silence_rms = None if silence_db is None else 10.0 ** (silence_db / 20.0)
        # C++ version of detect() (cpp/src/dsp/Yin.cpp) when built; see dsfp_tune/native.py.
        self._native = (
            _native.Yin(self.sample_rate, self.f_min, self.f_max, self.window, self.threshold, silence_db)
            if _native is not None
            else None
        )

    @property
    def latency_seconds(self) -> float:
        """Duration of one analysis frame."""
        return self.frame_length / self.sample_rate

    def cmnd(self, frame: np.ndarray) -> np.ndarray:
        """Steps 1-2 for one frame: the ``d'(tau)`` curve (handy for plotting)."""
        d = difference_function(frame, self.window, self.tau_max, self.n_fft)
        return cumulative_mean_normalized_difference(d)

    def detect(self, frame: np.ndarray) -> PitchEstimate:
        """Estimate f0 for one frame of at least :attr:`frame_length` samples."""
        frame = np.asarray(frame)
        if frame.ndim != 1:
            raise ValueError("frame must be 1-D (mono)")
        if len(frame) < self.frame_length:
            raise ValueError(f"frame has {len(frame)} samples, need frame_length = {self.frame_length}")
        frame = frame[: self.frame_length]

        if self._native is not None:
            f0, aperiodicity, voiced = self._native.detect(frame)
            return PitchEstimate(f0, aperiodicity, voiced)

        if self._silence_rms is not None:
            rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
            if rms < self._silence_rms:
                return PitchEstimate(math.nan, 1.0, False)

        cmnd = self.cmnd(frame)
        tau, found = absolute_threshold(cmnd, self.tau_min, self.threshold)
        tau_exact, aperiodicity = parabolic_interpolation(cmnd, tau)
        aperiodicity = max(0.0, aperiodicity)

        if not found:
            return PitchEstimate(math.nan, aperiodicity, False)
        return PitchEstimate(self.sample_rate / tau_exact, aperiodicity, True)


class YinStream:
    """Real-time wrapper: feed audio blocks of any size, get an estimate every ``hop_length`` samples.

    Keeps the most recent :attr:`Yin.frame_length` samples in a buffer (initially silence)
    and runs :meth:`Yin.detect` on it each time ``hop_length`` new samples have arrived::

        stream = YinStream(44100, hop_length=256)
        for block in mic:
            for est in stream.push(block):
                ...

    Each estimate describes the buffer ending at the latest sample, so it lags the input
    by about half a frame (plus the hop).
    """

    def __init__(self, sample_rate: int, hop_length: int = 256, **yin_kwargs) -> None:
        if hop_length < 1:
            raise ValueError("hop_length must be positive")
        self.yin = Yin(sample_rate, **yin_kwargs)
        self.hop_length = int(hop_length)
        self._buf = np.zeros(self.yin.frame_length, dtype=np.float64)
        self._since_last = 0

    def reset(self) -> None:
        self._buf[:] = 0.0
        self._since_last = 0

    def push(self, block: np.ndarray) -> list[PitchEstimate]:
        """Append samples; return the estimates (possibly none) completed by this block."""
        block = np.asarray(block, dtype=np.float64).ravel()
        out: list[PitchEstimate] = []
        pos = 0
        while pos < len(block):
            take = min(self.hop_length - self._since_last, len(block) - pos)
            self._buf[:-take] = self._buf[take:]
            self._buf[-take:] = block[pos : pos + take]
            pos += take
            self._since_last += take
            if self._since_last == self.hop_length:
                out.append(self.yin.detect(self._buf))
                self._since_last = 0
        return out


# ---------------------------------------------------------------------------
# Offline tracking over a whole signal
# ---------------------------------------------------------------------------


@dataclass
class PitchTrack:
    """f0 over time. All arrays have one entry per frame.

    times:
        Centre of each analysis frame, in seconds.
    f0:
        Hz, ``nan`` where unvoiced.
    """

    times: np.ndarray
    f0: np.ndarray
    aperiodicity: np.ndarray
    voiced: np.ndarray
    sample_rate: int
    hop_length: int


def yin_track(
    samples: np.ndarray,
    sample_rate: int,
    hop_length: int = 256,
    **yin_kwargs,
) -> PitchTrack:
    """Run :class:`Yin` over a whole mono signal, one frame every ``hop_length`` samples.

    Extra keyword arguments go to :class:`Yin` (``f_min``, ``f_max``, ``threshold``, ...).
    The end of the signal is zero-padded so the final samples are analysed.
    """
    samples = np.asarray(samples)
    if samples.ndim != 1:
        raise ValueError("samples must be 1-D (mono); use AudioSignal.to_mono()")
    if hop_length < 1:
        raise ValueError("hop_length must be positive")

    yin = Yin(sample_rate, **yin_kwargs)
    n = yin.frame_length
    n_frames = max(1, int(math.ceil(max(len(samples) - n, 0) / hop_length)) + 1)
    padded = np.zeros((n_frames - 1) * hop_length + n, dtype=np.float64)
    padded[: len(samples)] = samples

    f0 = np.full(n_frames, np.nan)
    aper = np.ones(n_frames)
    voiced = np.zeros(n_frames, dtype=bool)
    for i in range(n_frames):
        start = i * hop_length
        est = yin.detect(padded[start : start + n])
        f0[i], aper[i], voiced[i] = est.f0, est.aperiodicity, est.voiced

    times = (np.arange(n_frames) * hop_length + n / 2) / sample_rate
    return PitchTrack(times, f0, aper, voiced, int(sample_rate), int(hop_length))
