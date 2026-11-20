"""Pitch correction (autotune): detect pitch, choose the target note, shift toward it.

    audio ──► YinStream (f0) ──► NoteSnapper (target note) ──► retune smoothing ──► FrequencyShifter
                                  key from dsfp_tune.theory      (glide speed)       (ratio = 2**(c/12))

* :class:`NoteSnapper` — nearest allowed note (any semitone, or only notes of a key) with
  hysteresis so the target doesn't flicker between two notes.
* :class:`PitchCorrector` — the streaming pipeline: ``out = corrector.process(block)``.
  Works on blocks of any size, so the same object serves the microphone in real time and
  whole files offline.
* :func:`correct_pitch` — convenience for a whole signal (latency removed).

Retune speed: the applied correction (in semitones) moves toward the target correction with
time constant ``retune_ms``. 0 ms snaps instantly (robotic, vibrato removed); 50-200 ms
glides naturally and keeps vibrato.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from dsfp_tune.analysis import PitchEstimate, YinStream
from dsfp_tune.synthesis import FrequencyShifter
from dsfp_tune.theory import Key, freq_to_midi, parse_key
from dsfp_tune.theory.notes import DEFAULT_A4

__all__ = ["CorrectionFrame", "CorrectionResult", "NoteSnapper", "PitchCorrector", "correct_pitch"]


class NoteSnapper:
    """Pick the target note (MIDI number) for a detected pitch.

    Parameters
    ----------
    key:
        ``None`` = any semitone (chromatic); a :class:`~dsfp_tune.theory.Key` or a string such
        as ``"A minor"`` restricts targets to that key's notes.
    hysteresis:
        Semitones. The current target is kept until another allowed note is closer by more
        than this, so a pitch hovering near the boundary doesn't flip back and forth.
    """

    def __init__(self, key: Key | str | None = None, hysteresis: float = 0.15) -> None:
        if isinstance(key, str):
            key = parse_key(key)
        self.key = key
        self.hysteresis = float(hysteresis)
        self._allowed = frozenset(key.pitch_classes) if key is not None else frozenset(range(12))
        self.current: int | None = None

    def nearest(self, midi: float) -> int:
        """Nearest allowed note, ignoring hysteresis."""
        base = int(round(midi))
        candidates = [n for n in range(base - 6, base + 7) if n % 12 in self._allowed]
        return min(candidates, key=lambda n: (abs(midi - n), n))

    def update(self, midi: float) -> int:
        """Target note for this frame (remembers it for hysteresis)."""
        best = self.nearest(midi)
        cur = self.current
        if cur is None or abs(midi - cur) > abs(midi - best) + self.hysteresis:
            self.current = best
        return self.current

    def reset(self) -> None:
        self.current = None


@dataclass(frozen=True)
class CorrectionFrame:
    """State after one pitch estimate.

    time:
        Centre of the analysed audio, in seconds of *input* time.
    target_midi:
        Target note, or ``nan`` when not correcting (unvoiced).
    correction:
        Applied shift in semitones (``ratio = 2 ** (correction / 12)``).
    """

    time: float
    f0: float
    voiced: bool
    target_midi: float
    correction: float
    ratio: float


class PitchCorrector:
    """Streaming autotune: feed audio blocks, get corrected audio blocks of the same length.

    Parameters
    ----------
    sample_rate:
        Audio sample rate (Hz).
    key:
        ``None`` (any semitone), a ``Key``, or a string like ``"C major"`` / ``"F# minor"``.
    retune_ms:
        Glide time constant toward the target. 0 = instant (hard autotune).
    hysteresis:
        See :class:`NoteSnapper`.
    hold_ms:
        Keep correcting through unvoiced gaps shorter than this (a frame or two of YIN
        dropout inside a note shouldn't release the correction).
    note_window_ms:
        The target note is chosen from the median pitch over this window, so vibrato that
        swings past the midpoint between two notes doesn't flip the target back and forth.
        (Each frame's *instantaneous* pitch is still what gets corrected.)
    note_jump:
        If the pitch moves more than this many semitones from the current target, switch
        notes immediately instead of waiting for the median (a real note change).
    max_correction:
        Never shift more than this many semitones (protects against octave errors).
    f_min, f_max, threshold:
        YIN settings (see :class:`~dsfp_tune.analysis.Yin`).
    n_fft, overlap:
        Shifter frame size and overlap (see :class:`~dsfp_tune.synthesis.FrequencyShifter`).
    hop_length:
        Samples between pitch estimates.
    keep_history:
        Store every :class:`CorrectionFrame` in :attr:`history` (for plots).
    """

    def __init__(
        self,
        sample_rate: int,
        key: Key | str | None = None,
        retune_ms: float = 25.0,
        hysteresis: float = 0.15,
        hold_ms: float = 60.0,
        note_window_ms: float = 120.0,
        note_jump: float = 0.75,
        max_correction: float = 2.0,
        f_min: float = 70.0,
        f_max: float = 1000.0,
        threshold: float = 0.1,
        n_fft: int = 2048,
        overlap: int = 4,
        hop_length: int = 256,
        a4: float = DEFAULT_A4,
        keep_history: bool = False,
    ) -> None:
        if retune_ms < 0:
            raise ValueError("retune_ms must be >= 0")
        self.sample_rate = int(sample_rate)
        self.hop_length = int(hop_length)
        self.a4 = float(a4)
        self.max_correction = float(max_correction)
        self.yin = YinStream(sample_rate, hop_length=hop_length, f_min=f_min, f_max=f_max, threshold=threshold)
        self.shifter = FrequencyShifter(n_fft, overlap)
        self.snapper = NoteSnapper(key, hysteresis)

        dt = hop_length / sample_rate
        self._alpha = 1.0 if retune_ms == 0 else 1.0 - math.exp(-dt * 1000.0 / retune_ms)
        self._hold_frames = int(round(hold_ms / 1000.0 / dt))
        self._recent: deque[float] = deque(maxlen=max(1, int(round(note_window_ms / 1000.0 / dt))))
        self._note_jump = float(note_jump)
        self._since_estimate = 0
        self._n_estimates = 0
        self._unvoiced_run = 0
        self._desired = 0.0
        self.correction = 0.0
        self.ratio = 1.0
        self.latest: CorrectionFrame | None = None
        self.history: list[CorrectionFrame] | None = [] if keep_history else None

    @property
    def key(self) -> Key | None:
        return self.snapper.key

    @property
    def latency(self) -> int:
        """Delay from input to output, in samples."""
        return self.shifter.latency

    def _update(self, est: PitchEstimate) -> None:
        if est.voiced:
            midi = float(freq_to_midi(est.f0, self.a4))
            cur = self.snapper.current
            if cur is not None and abs(midi - cur) > self._note_jump:
                self._recent.clear()  # a real note change: decide on the new pitch right away
            self._recent.append(midi)
            target = self.snapper.update(float(np.median(self._recent)))
            desired = target - midi
            if abs(desired) > self.max_correction:  # likely an octave/pitch error: leave it alone
                desired, target = 0.0, math.nan
            self._unvoiced_run = 0
        else:
            self._unvoiced_run += 1
            if self._unvoiced_run > self._hold_frames:
                self.snapper.reset()
                self._recent.clear()
                desired, target = 0.0, math.nan
            else:  # brief dropout inside a note: hold the previous correction
                desired = self._desired
                target = self.snapper.current if self.snapper.current is not None else math.nan

        self._desired = desired
        self.correction += self._alpha * (desired - self.correction)
        if desired == 0.0 and abs(self.correction) < 0.005:  # < 0.5 cent: bypass exactly
            self.correction = 0.0
        self.ratio = 2.0 ** (self.correction / 12.0)

        self._n_estimates += 1
        end = self._n_estimates * self.hop_length
        frame = CorrectionFrame(
            time=(end - self.yin.yin.frame_length / 2) / self.sample_rate,
            f0=est.f0,
            voiced=est.voiced,
            target_midi=float(target),
            correction=self.correction,
            ratio=self.ratio,
        )
        self.latest = frame
        if self.history is not None:
            self.history.append(frame)

    def process(self, block: np.ndarray) -> np.ndarray:
        """Correct one block. Returns ``len(block)`` samples, delayed by :attr:`latency`."""
        block = np.asarray(block, dtype=np.float64).ravel()
        out = np.empty(len(block))
        pos = 0
        while pos < len(block):
            # Step in pitch-estimate-sized pieces so each new estimate sets the ratio
            # for the audio right after it.
            take = min(self.hop_length - self._since_estimate, len(block) - pos)
            chunk = block[pos : pos + take]
            for est in self.yin.push(chunk):
                self._update(est)
            out[pos : pos + take] = self.shifter.process(chunk, self.ratio)
            pos += take
            self._since_estimate = (self._since_estimate + take) % self.hop_length
        return out


@dataclass
class CorrectionResult:
    """Output of :func:`correct_pitch`. Arrays (``times`` etc.) have one entry per pitch estimate."""

    samples: np.ndarray
    sample_rate: int
    times: np.ndarray
    f0: np.ndarray
    voiced: np.ndarray
    target_midi: np.ndarray
    correction: np.ndarray
    key: Key | None


def correct_pitch(samples: np.ndarray, sample_rate: int, **kwargs) -> CorrectionResult:
    """Autotune a whole mono signal. Keyword arguments go to :class:`PitchCorrector`.

    The returned audio has the same length as the input and no delay.
    """
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim != 1:
        raise ValueError("samples must be 1-D (mono)")
    pc = PitchCorrector(sample_rate, keep_history=True, **kwargs)
    lat = pc.latency
    y = pc.process(np.concatenate([x, np.zeros(lat)]))[lat : lat + len(x)]

    duration = len(x) / sample_rate
    hist = [h for h in pc.history if h.time <= duration]
    return CorrectionResult(
        samples=y.astype(np.float32),
        sample_rate=int(sample_rate),
        times=np.array([h.time for h in hist]),
        f0=np.array([h.f0 for h in hist]),
        voiced=np.array([h.voiced for h in hist], dtype=bool),
        target_midi=np.array([h.target_midi for h in hist]),
        correction=np.array([h.correction for h in hist]),
        key=pc.key,
    )
