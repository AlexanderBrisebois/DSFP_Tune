"""FFT peak-picking pitch detector: read the frequency straight off the Fourier transform.

This is the "direct" alternative to :mod:`dsfp_tune.analysis.yin`, there so the two can be
compared. For each frame:

1. Hann-window the frame and take a zero-padded real FFT.
2. In the bins between ``f_min`` and ``f_max``, take the **largest** magnitude peak.
3. Refine it to sub-bin accuracy with a parabola through the log magnitudes of the peak bin
   and its two neighbours (quadratic interpolation of the spectral peak):

       a, b, c = 20 log10 |X[k-1]|, |X[k]|, |X[k+1]|
       p       = 0.5 (a - c) / (a - 2b + c)          (in -0.5 .. 0.5)
       f0      = (k + p) * sample_rate / n_fft

4. Voiced/unvoiced: the frame is voiced when it is above the silence gate and the peak
   stands out from the rest of the search band. ``aperiodicity`` is the median magnitude in
   the band divided by the peak magnitude (0 = one clean peak, 1 = flat noise); the frame is
   voiced when it is below ``threshold`` (default 0.1, i.e. the peak is >= 20 dB above the
   median bin).

What to expect compared with YIN
--------------------------------
* The strongest peak is not always the fundamental. In sung vowels the 2nd or 3rd harmonic
  is often louder than the 1st, and then this detector reports an octave (or a twelfth) too
  high. That is the honest behaviour of the direct method and what the comparison is for.
  (Harmonic product spectrum or cepstrum would fix much of it; they are not used here.)
* Frequency resolution comes from the frame length: bins are ``sample_rate / n_fft`` apart
  and two harmonics only separate if the frame holds a few periods, so the frame is longer
  (more latency) than YIN's for the same ``f_min``.

Same interface as :class:`~dsfp_tune.analysis.Yin`: ``detect(frame) -> PitchEstimate``,
``frame_length``, ``latency_seconds``. Pick a method with :func:`make_pitch_detector`, or pass
``method="fft"`` to :class:`~dsfp_tune.analysis.YinStream`, :func:`~dsfp_tune.analysis.yin_track`,
:class:`~dsfp_tune.analysis.HeldPitchStream` and :class:`~dsfp_tune.correction.PitchCorrector`.
"""

from __future__ import annotations

import math

import numpy as np

from dsfp_tune.analysis.yin import PitchEstimate, Yin

__all__ = ["FftPitch", "PITCH_METHODS", "make_pitch_detector", "interpolate_peak"]

#: Names accepted by :func:`make_pitch_detector` (and the ``--pitch`` CLI flag).
PITCH_METHODS = ("yin", "fft")


def interpolate_peak(log_mag: np.ndarray, k: int) -> tuple[float, float]:
    """Quadratic interpolation of a spectral peak at bin ``k``.

    ``log_mag`` should be log magnitudes (dB); a parabola through a Hann window's main lobe
    in dB is a close fit. Returns ``(bin_position, peak_level)``. At the array edges, or if
    ``k`` is not a local maximum, returns ``k`` unchanged.
    """
    if k <= 0 or k >= len(log_mag) - 1:
        return float(k), float(log_mag[k])
    a, b, c = float(log_mag[k - 1]), float(log_mag[k]), float(log_mag[k + 1])
    denom = a - 2.0 * b + c
    if denom >= 0:  # flat or not a maximum
        return float(k), b
    p = 0.5 * (a - c) / denom
    return k + p, b - 0.25 * (a - c) * p


class FftPitch:
    """Frame-by-frame pitch from the largest FFT peak, with parabolic interpolation.

    Parameters
    ----------
    sample_rate:
        Sample rate of the audio, in Hz.
    f_min, f_max:
        Search band in Hz: only peaks between these are considered.
    periods:
        Frame length as a number of periods of ``f_min`` (default 4). With a Hann window two
        neighbouring harmonics are only resolved when the frame holds more than ~2 periods of
        their spacing, so 4 periods resolves harmonics down to ``f_min``.
    frame_length:
        Frame length in samples; overrides ``periods``.
    zero_pad:
        FFT size is the next power of two >= ``zero_pad * frame_length`` (default 2), which
        makes the bins finer before the interpolation.
    threshold:
        Largest allowed ``aperiodicity`` (median-to-peak magnitude ratio in the band) for a
        voiced frame. 0.1 = the peak must be 20 dB above the median bin.
    silence_db:
        Frames with RMS below this (dBFS) are unvoiced without analysis. ``None`` disables it.
    """

    method = "fft"

    def __init__(
        self,
        sample_rate: int,
        f_min: float = 70.0,
        f_max: float = 1000.0,
        periods: float = 4.0,
        frame_length: int | None = None,
        zero_pad: int = 2,
        threshold: float = 0.1,
        silence_db: float | None = -60.0,
    ) -> None:
        if not 0 < f_min < f_max:
            raise ValueError("need 0 < f_min < f_max")
        if f_max >= sample_rate / 2:
            raise ValueError(f"f_max={f_max} must be below Nyquist ({sample_rate / 2} Hz)")
        if not 0 < threshold < 1:
            raise ValueError("threshold must be in (0, 1)")
        if zero_pad < 1:
            raise ValueError("zero_pad must be >= 1")
        self.sample_rate = int(sample_rate)
        self.f_min = float(f_min)
        self.f_max = float(f_max)
        self.threshold = float(threshold)
        self.silence_db = silence_db

        if frame_length is None:
            if periods <= 0:
                raise ValueError("periods must be > 0")
            frame_length = int(math.ceil(periods * sample_rate / f_min))
        #: Samples needed per call to :meth:`detect` (the analysis latency).
        self.frame_length = int(frame_length)
        if self.frame_length < 16:
            raise ValueError("frame_length too short")
        self.n_fft = 1 << (zero_pad * self.frame_length - 1).bit_length()
        self.window = np.hanning(self.frame_length)
        bin_hz = self.sample_rate / self.n_fft
        # Search bins; one extra bin each side so a peak at the band edge can still be interpolated.
        self.k_min = max(1, int(math.floor(self.f_min / bin_hz)))
        self.k_max = min(self.n_fft // 2 - 1, int(math.ceil(self.f_max / bin_hz)))
        self._silence_rms = None if silence_db is None else 10.0 ** (silence_db / 20.0)

    @property
    def latency_seconds(self) -> float:
        """Duration of one analysis frame."""
        return self.frame_length / self.sample_rate

    @property
    def bin_hz(self) -> float:
        """FFT bin spacing (before interpolation), in Hz."""
        return self.sample_rate / self.n_fft

    def spectrum(self, frame: np.ndarray) -> np.ndarray:
        """Magnitude spectrum ``|X[k]|`` of the windowed, zero-padded frame (handy for plotting)."""
        x = np.asarray(frame[: self.frame_length], dtype=np.float64)
        return np.abs(np.fft.rfft(x * self.window, self.n_fft))

    def detect(self, frame: np.ndarray) -> PitchEstimate:
        """Estimate f0 for one frame of at least :attr:`frame_length` samples."""
        frame = np.asarray(frame)
        if frame.ndim != 1:
            raise ValueError("frame must be 1-D (mono)")
        if len(frame) < self.frame_length:
            raise ValueError(f"frame has {len(frame)} samples, need frame_length = {self.frame_length}")
        frame = frame[: self.frame_length].astype(np.float64)

        if self._silence_rms is not None:
            rms = math.sqrt(float(np.dot(frame, frame)) / len(frame))
            if rms < self._silence_rms:
                return PitchEstimate(math.nan, 1.0, False)

        mag = self.spectrum(frame)
        band = mag[self.k_min : self.k_max + 1]
        peak = float(band.max())
        if peak <= 0.0:
            return PitchEstimate(math.nan, 1.0, False)
        k = self.k_min + int(np.argmax(band))
        log_mag = 20.0 * np.log10(mag[k - 1 : k + 2] + 1e-12)
        pos, _ = interpolate_peak(log_mag, 1)
        f0 = (k - 1 + pos) * self.bin_hz

        aperiodicity = min(1.0, float(np.median(band)) / peak)
        voiced = aperiodicity < self.threshold and self.f_min <= f0 <= self.f_max
        return PitchEstimate(f0 if voiced else math.nan, aperiodicity, bool(voiced))


def make_pitch_detector(method: str, sample_rate: int, **kwargs) -> Yin | FftPitch:
    """``"yin"`` -> :class:`~dsfp_tune.analysis.Yin`, ``"fft"`` -> :class:`FftPitch`.

    Keyword arguments go to that class. ``window`` is YIN-only and ``periods``/``frame_length``/
    ``zero_pad`` are FFT-only; the shared ones are ``f_min``, ``f_max``, ``threshold``,
    ``silence_db``.
    """
    method = str(method).lower()
    if method == "yin":
        return Yin(sample_rate, **kwargs)
    if method == "fft":
        return FftPitch(sample_rate, **kwargs)
    raise ValueError(f"pitch method must be one of {', '.join(PITCH_METHODS)}, got {method!r}")
