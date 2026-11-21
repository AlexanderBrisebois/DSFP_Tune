"""Pitch hold: steady the YIN output so short detection failures don't leave gaps.

YIN marks a frame unvoiced when its dip isn't below the threshold. On real singing that
happens for a few frames at a time (breathy tone, a noisy room, vibrato), and occasionally it
jumps an octave. Everything downstream (correction, chord harmony, the pitch view) then
drops out for a moment. :class:`PitchHold` sits after YIN and fixes that without changing it:

* **Carry-over:** an unvoiced frame inside a note gets the last good pitch, for up to
  ``hold_ms``, as long as the input is still loud (within ``level_db`` of the note). Real
  silence, or a long consonant/breath, still ends the note.
* **Octave glitches:** a sudden jump of about an octave (x2 or x0.5) is treated as a
  detection error and the previous pitch is held, unless the new pitch lasts ``octave_ms``.
* **Median:** the reported pitch is the median of the last ``median`` good frames
  (3 removes single-frame spikes at the cost of about one frame of lag; 1 = off).

Held frames come back as :class:`HeldEstimate` with ``voiced=True`` and ``held=True``.

    stream = HeldPitchStream(44100, hop_length=256)     # drop-in for YinStream
    for est in stream.push(block): ...

    track = hold_track(yin_track(x, sr), x)             # offline, same result as streaming
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace

import numpy as np

from dsfp_tune.analysis.yin import PitchEstimate, PitchTrack, YinStream

__all__ = ["HeldEstimate", "HeldPitchStream", "PitchHold", "hold_track"]


@dataclass(frozen=True)
class HeldEstimate(PitchEstimate):
    """A :class:`PitchEstimate` after the hold. ``held`` = the pitch was carried over or
    replaced (YIN failed or jumped an octave in this frame)."""

    held: bool = False


class PitchHold:
    """Frame-by-frame hold. Call :meth:`update` once per YIN estimate.

    Parameters
    ----------
    sample_rate, hop_length:
        Of the estimates (to turn milliseconds into frames).
    hold_ms:
        Longest gap that is bridged. 0 turns the carry-over off.
    level_db:
        Only bridge while the input is no more than this many dB below the note's level.
    octave_ms:
        An octave jump that lasts longer than this is accepted as a real change.
    median:
        Number of good frames in the median (odd; 1 = off).
    """

    def __init__(self, sample_rate: int, hop_length: int, hold_ms: float = 300.0, level_db: float = 12.0,
                 octave_ms: float = 80.0, median: int = 3) -> None:
        if hold_ms < 0 or octave_ms < 0 or median < 1:
            raise ValueError("hold_ms and octave_ms must be >= 0 and median >= 1")
        frames = lambda ms: int(round(ms / 1000.0 * sample_rate / hop_length))  # noqa: E731
        self.hold_frames = frames(hold_ms)
        self.octave_frames = frames(octave_ms)
        self.level = 10.0 ** (-abs(level_db) / 20.0)
        self._recent: deque[float] = deque(maxlen=int(median))
        self.reset()

    def reset(self) -> None:
        self.last: float | None = None   # last reported pitch (Hz)
        self._recent.clear()
        self._gap = 0                    # frames bridged since the last good one
        self._octave_run = 0
        self._note_rms = 0.0

    def _is_octave_jump(self, f0: float) -> bool:
        st = 12.0 * math.log2(f0 / self.last)
        return abs(abs(st) - 12.0) < 1.0

    def update(self, est: PitchEstimate, rms: float) -> HeldEstimate:
        """Hold-corrected version of ``est``. ``rms`` is the input level of the newest hop."""
        if est.voiced and self.last is not None and self._is_octave_jump(est.f0):
            self._octave_run += 1
            if self._octave_run <= self.octave_frames:     # likely an octave error: keep the note
                return HeldEstimate(self.last, est.aperiodicity, True, held=True)
            self._recent.clear()                           # it lasted: a real octave leap
        if est.voiced:
            self._octave_run = 0
            self._gap = 0
            self._recent.append(est.f0)
            self._note_rms = rms if not self._note_rms else 0.9 * self._note_rms + 0.1 * rms
            self.last = float(np.median(self._recent))
            return HeldEstimate(self.last, est.aperiodicity, True, held=False)
        if self.last is not None and self._gap < self.hold_frames and rms >= self.level * self._note_rms:
            self._gap += 1
            return HeldEstimate(self.last, est.aperiodicity, True, held=True)
        self.reset()
        return HeldEstimate(est.f0, est.aperiodicity, False, held=False)


class HeldPitchStream(YinStream):
    """:class:`~dsfp_tune.analysis.YinStream` with a :class:`PitchHold` on its output.

    Extra keyword arguments ``hold_ms``, ``level_db``, ``octave_ms``, ``median`` go to the hold;
    the rest go to :class:`~dsfp_tune.analysis.Yin` (or, with ``method="fft"``, to
    :class:`~dsfp_tune.analysis.FftPitch`).
    """

    def __init__(self, sample_rate: int, hop_length: int = 256, hold_ms: float = 300.0, level_db: float = 12.0,
                 octave_ms: float = 80.0, median: int = 3, **yin_kwargs) -> None:
        super().__init__(sample_rate, hop_length, **yin_kwargs)
        self.hold = PitchHold(sample_rate, hop_length, hold_ms, level_db, octave_ms, median)

    def reset(self) -> None:
        super().reset()
        if hasattr(self, "hold"):
            self.hold.reset()

    def push(self, block: np.ndarray) -> list[HeldEstimate]:
        block = np.asarray(block, dtype=np.float64).ravel()
        out: list[HeldEstimate] = []
        pos = 0
        while pos < len(block):  # same stepping as YinStream.push, plus the level of each hop
            take = min(self.hop_length - self._since_last, len(block) - pos)
            self._buf[:-take] = self._buf[take:]
            self._buf[-take:] = block[pos : pos + take]
            pos += take
            self._since_last += take
            if self._since_last == self.hop_length:
                last_hop = self._buf[-self.hop_length :]
                rms = math.sqrt(float(np.dot(last_hop, last_hop)) / self.hop_length)
                out.append(self.hold.update(self.yin.detect(self._buf), rms))
                self._since_last = 0
        return out


def hold_track(track: PitchTrack, samples: np.ndarray, frame_length: int | None = None, **hold_kwargs) -> PitchTrack:
    """Apply :class:`PitchHold` to an offline :func:`~dsfp_tune.analysis.yin_track` result.

    ``samples`` is the signal the track came from (for the input level). ``frame_length`` is
    YIN's frame length; by default it is recovered from ``track.times``. Returns a new track
    whose ``voiced``/``f0`` include the held frames, plus a ``held`` attribute (bool array).
    """
    hop = track.hop_length
    if frame_length is None:
        frame_length = int(round(2 * track.times[0] * track.sample_rate)) if len(track.times) else hop
    n = frame_length
    x = np.asarray(samples, dtype=np.float64)
    hold = PitchHold(track.sample_rate, hop, **hold_kwargs)
    f0, voiced, held = track.f0.copy(), track.voiced.copy(), np.zeros(len(track.f0), dtype=bool)
    for i in range(len(f0)):
        end = i * hop + n                       # frame i covers [i*hop, i*hop + n)
        seg = x[max(0, end - hop) : end]
        rms = math.sqrt(float(np.dot(seg, seg)) / hop)
        est = hold.update(PitchEstimate(float(track.f0[i]), float(track.aperiodicity[i]), bool(track.voiced[i])), rms)
        f0[i], voiced[i], held[i] = est.f0, est.voiced, est.held
    out = replace(track, f0=f0, voiced=voiced)
    out.held = held
    return out
