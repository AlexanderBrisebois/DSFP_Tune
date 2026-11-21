"""FFT frequency shifter: move each frequency f to f * ratio (pitch shift, same duration).

This is the working, streaming version of the idea in ``scripts/freq_shift.py`` (Guinevere):
take the spectrum, move the power at frequency ``f`` to ``f * shift``, and inverse-FFT.
Making that sound right on real audio needs three extra pieces:

1. **Short overlapping frames (STFT).** Audio is cut into Hann-windowed frames of
   ``n_fft`` samples every ``hop = n_fft / overlap`` samples; each frame is shifted and the
   results are overlap-added. This lets the shift amount change over time (autotune).
2. **Shift every harmonic, not only the fundamental.** A voice is a fundamental plus
   harmonics at 2f, 3f, ...; all of them move by the same *ratio*, so bin ``k`` moves to
   bin ``k * ratio``.
3. **Phase tracking (phase vocoder).** FFT bins are ~21 Hz wide at 44.1 kHz / 2048, too
   coarse to place a frequency exactly. The phase change of each bin between frames gives
   its true frequency; the shifted bin's phase is then advanced at ``true_freq * ratio``
   so consecutive frames join without clicks.

With ``ratio == 1`` the output equals the input (delayed by :attr:`FrequencyShifter.latency`).
Formants move with the pitch (large shifts sound "chipmunk"); for autotune corrections of
a semitone or two this is usually acceptable. Formant preservation is a later improvement.

Usage::

    y = freq_shift(x, 2 ** (1 / 12))          # whole signal up one semitone

    shifter = FrequencyShifter(n_fft=2048)      # streaming, ratio may change per block
    out_block = shifter.process(in_block, ratio)
"""

from __future__ import annotations

import numpy as np

from dsfp_tune.native import lib as _native

__all__ = ["FrequencyShifter", "freq_shift"]

_TWO_PI = 2.0 * np.pi


class FrequencyShifter:
    """Streaming phase-vocoder pitch shifter.

    Parameters
    ----------
    n_fft:
        Frame length (power of two). Longer = better frequency resolution for low voices,
        more latency. 2048 at 44.1 kHz is 46 ms; 1024 is 23 ms.
    overlap:
        Frames per ``n_fft`` (hop = ``n_fft / overlap``). 4 is the usual minimum for Hann.

    :meth:`process` returns exactly as many samples as it is given; the output is the shifted
    input delayed by :attr:`latency` samples.
    """

    def __init__(self, n_fft: int = 2048, overlap: int = 4) -> None:
        if n_fft < 16 or n_fft & (n_fft - 1):
            raise ValueError("n_fft must be a power of two >= 16")
        if overlap < 2 or n_fft % overlap:
            raise ValueError("overlap must be >= 2 and divide n_fft")
        self.n_fft = int(n_fft)
        self.overlap = int(overlap)
        self.hop = self.n_fft // self.overlap
        self.window = 0.5 - 0.5 * np.cos(_TWO_PI * np.arange(self.n_fft) / self.n_fft)  # periodic Hann
        # Analysis x synthesis window overlap-adds to this constant; divide it out.
        self._ola_norm = float(np.sum(self.window**2) / self.hop)
        self._n_bins = self.n_fft // 2 + 1
        self._k = np.arange(self._n_bins, dtype=np.float64)
        self._rad_per_bin = _TWO_PI * self.hop / self.n_fft  # phase advance per hop, per bin of frequency
        #: Delay from input to output, in samples.
        self.latency = self.n_fft - 1
        # C++ version (cpp/src/dsp/FrequencyShifter.cpp) when built; see dsfp_tune/native.py.
        self._native = _native.FrequencyShifter(self.n_fft, self.overlap) if _native is not None else None
        self.reset()

    def reset(self) -> None:
        self._in = np.zeros(self.n_fft)
        self._acc = np.zeros(self.n_fft)
        self._since_frame = 0
        self._last_phase = np.zeros(self._n_bins)
        self._sum_phase = np.zeros(self._n_bins)
        # hop-1 samples of silence up front make the output length always match the input.
        self._pending = np.zeros(self.hop - 1)
        self.frames_processed = 0
        if self._native is not None:
            self._native.reset()

    def _frame(self, ratio: float) -> np.ndarray:
        spec = np.fft.rfft(self._in * self.window)
        mag = np.abs(spec)
        phase = np.angle(spec)

        # Analysis: true frequency of each bin (in bins) from its phase advance since last frame.
        dphi = phase - self._last_phase
        self._last_phase = phase
        dev = dphi - self._rad_per_bin * self._k
        dev -= _TWO_PI * np.round(dev / _TWO_PI)  # wrap to [-pi, pi]
        true_bin = self._k + dev / self._rad_per_bin

        if ratio == 1.0:
            # Pass-through: re-sync synthesis phase to the input, so the output is exact.
            self._sum_phase = phase
            syn_mag = mag
        else:
            # Move bin k (and its true frequency) to bin round(k * ratio). Bins landing on the
            # same target add their power; the frequency is their magnitude-weighted mean.
            target = np.round(self._k * ratio).astype(np.intp)
            ok = target < self._n_bins
            syn_mag = np.bincount(target[ok], weights=mag[ok], minlength=self._n_bins)
            weighted = np.bincount(target[ok], weights=mag[ok] * true_bin[ok] * ratio, minlength=self._n_bins)
            syn_bin = np.where(syn_mag > 0, weighted / np.maximum(syn_mag, 1e-30), self._k)
            # Synthesis: advance each output bin's phase at its (shifted) true frequency.
            self._sum_phase = np.mod(self._sum_phase + self._rad_per_bin * syn_bin, _TWO_PI)

        frame = np.fft.irfft(syn_mag * np.exp(1j * self._sum_phase), self.n_fft)
        self._acc += frame * self.window / self._ola_norm
        out = self._acc[: self.hop].copy()
        self._acc[: -self.hop] = self._acc[self.hop :]
        self._acc[-self.hop :] = 0.0
        self.frames_processed += 1
        return out

    def process(self, block: np.ndarray, ratio: float = 1.0) -> np.ndarray:
        """Shift ``block`` by ``ratio`` (frequency multiplier: 2 = up an octave, 2**(1/12) = up a
        semitone). Returns ``len(block)`` samples. ``ratio`` applies to frames completed during
        this call, so it can change from block to block."""
        ratio = float(ratio)
        if not 0.25 <= ratio <= 4.0:
            raise ValueError(f"ratio {ratio} out of range [0.25, 4]")
        block = np.asarray(block, dtype=np.float64).ravel()
        if self._native is not None:
            return self._native.process(block, ratio)
        pieces = [self._pending]
        pos = 0
        while pos < len(block):
            take = min(self.hop - self._since_frame, len(block) - pos)
            self._in[:-take] = self._in[take:]
            self._in[-take:] = block[pos : pos + take]
            pos += take
            self._since_frame += take
            if self._since_frame == self.hop:
                pieces.append(self._frame(ratio))
                self._since_frame = 0
        buf = np.concatenate(pieces)
        out, self._pending = buf[: len(block)], buf[len(block) :]
        return out


def freq_shift(samples: np.ndarray, ratio: float, n_fft: int = 2048, overlap: int = 4) -> np.ndarray:
    """Shift a whole signal's frequencies by ``ratio``. Same length as the input, no delay."""
    x = np.asarray(samples, dtype=np.float64).ravel()
    shifter = FrequencyShifter(n_fft, overlap)
    lat = shifter.latency
    y = shifter.process(np.concatenate([x, np.zeros(lat)]), ratio)
    return y[lat : lat + len(x)]
