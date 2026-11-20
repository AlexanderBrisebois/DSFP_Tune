"""Pitch correction (autotune): detect pitch, choose the target note, shift toward it.

    audio ──► YinStream (f0) ──► NoteSnapper (target note) ──► retune smoothing ──► FrequencyShifter
                                  key from dsfp_tune.theory      (glide speed)       (ratio = 2**(c/12))

* :class:`NoteSnapper` — nearest allowed note (any semitone, or only notes of a key) with
  hysteresis so the target doesn't flicker between two notes.
* :class:`PitchCorrector` — the streaming pipeline: ``out = corrector.process(block)``.
  Works on blocks of any size, so the same object serves the microphone in real time and
  whole files offline.
* :func:`correct_pitch` — convenience for a whole signal (latency removed).

Chord harmony (``harmony > 0``, needs a 7-note key): the snapped note is the root of a triad
built from the key (C major + A -> A minor: A, C, E). The third and fifth are two more shifted
copies of the input, tuned to those notes and mixed under the lead at level ``harmony``.

Retune speed (attack): the applied correction (in semitones) moves toward the target correction
with time constant ``retune_ms``. 0 ms snaps instantly (robotic, vibrato removed); 50-200 ms
glides naturally and keeps vibrato. ``release_ms`` is the time constant for letting go when a
note ends or its pitch is lost, and ``amount`` (0-1) scales how far notes are pulled.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from dsfp_tune.analysis import PitchEstimate, YinStream
from dsfp_tune.synthesis import FrequencyShifter
from dsfp_tune.theory import Key, diatonic_triad, freq_to_midi, parse_key
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
    chord: tuple[int, ...] = ()  #: MIDI notes of the added triad (root, third, fifth); () = none


class PitchCorrector:
    """Streaming autotune: feed audio blocks, get corrected audio blocks of the same length.

    Parameters
    ----------
    sample_rate:
        Audio sample rate (Hz).
    key:
        ``None`` (any semitone), a ``Key``, or a string like ``"C major"`` / ``"F# minor"``.
    retune_ms:
        Attack: glide time constant toward the target note. 0 = instant (hard autotune).
    release_ms:
        Release: time constant for letting go of the correction (back to the sung pitch) when
        a note ends, its pitch is lost, or a correction is rejected. ``None`` = same as
        ``retune_ms``.
    amount:
        Correction strength, 0-1. 1 pulls notes all the way onto the target; 0.5 halfway
        (keeps some of the original intonation); 0 leaves the pitch alone.
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
    harmony:
        Level of each added chord voice (third and fifth) relative to the lead, e.g. 0.5.
        0 (default) = off. Needs a 7-note ``key``.
    chord_hold_ms:
        Once a chord starts it is held through pitch-detection dropouts for up to this long
        while the input stays loud (within 12 dB of the note), so a breathy or noisy note keeps
        its chord. It fades out when the singer stops, or the pitch is lost for longer.
    keep_history:
        Store every :class:`CorrectionFrame` in :attr:`history` (for plots).
    """

    def __init__(
        self,
        sample_rate: int,
        key: Key | str | None = None,
        retune_ms: float = 25.0,
        release_ms: float | None = None,
        amount: float = 1.0,
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
        harmony: float = 0.0,
        chord_hold_ms: float = 500.0,
        keep_history: bool = False,
    ) -> None:
        if release_ms is None:
            release_ms = retune_ms
        for name, value in (("retune_ms", retune_ms), ("release_ms", release_ms), ("hold_ms", hold_ms),
                            ("note_window_ms", note_window_ms), ("hysteresis", hysteresis)):
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if not 0.0 <= amount <= 1.0:
            raise ValueError("amount must be between 0 and 1")
        if note_jump <= 0 or max_correction <= 0:
            raise ValueError("note_jump and max_correction must be > 0")
        if harmony < 0:
            raise ValueError("harmony must be >= 0")
        self.sample_rate = int(sample_rate)
        self.hop_length = int(hop_length)
        self.a4 = float(a4)
        self.max_correction = float(max_correction)
        self.amount = float(amount)
        self.yin = YinStream(sample_rate, hop_length=hop_length, f_min=f_min, f_max=f_max, threshold=threshold)
        self.shifter = FrequencyShifter(n_fft, overlap)
        self.snapper = NoteSnapper(key, hysteresis)
        self.harmony = float(harmony)
        if self.harmony > 0:
            if self.snapper.key is None:
                raise ValueError("harmony needs a key (chords are built from the key's notes)")
            diatonic_triad(self.snapper.key, self.snapper.key.tonic + 60)  # raises if not a 7-note scale
        # Third and fifth voices: separate shifters fed the same input, so latency matches the lead.
        self._voices = [FrequencyShifter(n_fft, overlap) for _ in range(2)] if self.harmony > 0 else []
        # Semitones from root to third and fifth. Kept from the last chord while muted, so the voice
        # shifters never run at ratio 1: the shifter's exact pass-through at 1 resets its phases, and
        # shifting again after that comes out about 4 dB quieter (no phase locking yet).
        self._intervals = (4, 7)
        self._chord: tuple[int, ...] = ()
        self.voice_ratios = tuple(2.0 ** (iv / 12.0) for iv in self._intervals)
        # Chord voice level 0..1: glides (15 ms) toward 1 while a note is snapped, toward 0 otherwise.
        self._env = 0.0
        self._env_target = 0.0
        self._env_next = 0.0
        self._env_decay = math.exp(-1.0 / (0.015 * sample_rate))
        # The level is decided on input time; delay it by the shifter latency to line up with the output.
        self._env_delay = np.zeros(self.shifter.latency)
        self._chord_hold_frames = int(round(chord_hold_ms / 1000.0 * sample_rate / hop_length))
        self._chord_held = 0          # estimates since the chord's note was last detected
        self._note_rms = 0.0          # input level of the current note (while detected)
        self._hop_energy = 0.0        # sum of squares of the input since the last estimate
        self._voice_correction = 0.0  # lead correction when the note was last detected

        dt = hop_length / sample_rate
        self._alpha = 1.0 if retune_ms == 0 else 1.0 - math.exp(-dt * 1000.0 / retune_ms)
        self._alpha_release = 1.0 if release_ms == 0 else 1.0 - math.exp(-dt * 1000.0 / release_ms)
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
            desired *= self.amount
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
        # Attack while pulling toward a note; release while letting go (nothing to correct).
        alpha = self._alpha if desired != 0.0 else self._alpha_release
        self.correction += alpha * (desired - self.correction)
        if desired == 0.0 and abs(self.correction) < 0.005:  # < 0.5 cent: bypass exactly
            self.correction = 0.0
        self.ratio = 2.0 ** (self.correction / 12.0)

        if self._voices:
            rms = math.sqrt(self._hop_energy / self.hop_length)
            self._hop_energy = 0.0
            if est.voiced and not math.isnan(target):
                root = int(target)
                if not self._chord or self._chord[0] != root:
                    self._chord = diatonic_triad(self.snapper.key, root)
                    self._intervals = (self._chord[1] - root, self._chord[2] - root)
                self._note_rms = rms if not self._note_rms else 0.9 * self._note_rms + 0.1 * rms
                self._chord_held = 0
                self._voice_correction = self.correction
                self._env_next = 1.0
            elif self._chord and self._chord_held < self._chord_hold_frames and rms > 0.25 * self._note_rms:
                # Pitch lost for a moment but the note is still sounding: keep the chord.
                self._chord_held += 1
                self._env_next = 1.0
            else:
                self._chord = ()
                self._note_rms = 0.0
                self._chord_held = 0
                self._env_next = 0.0
            self.voice_ratios = tuple(2.0 ** ((self._voice_correction + iv) / 12.0) for iv in self._intervals)

        self._n_estimates += 1
        end = self._n_estimates * self.hop_length
        frame = CorrectionFrame(
            time=(end - self.yin.yin.frame_length / 2) / self.sample_rate,
            f0=est.f0,
            voiced=est.voiced,
            target_midi=float(target),
            correction=self.correction,
            ratio=self.ratio,
            chord=self._chord,
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
            if self._voices:
                self._hop_energy += float(np.dot(chunk, chunk))
            for est in self.yin.push(chunk):
                self._update(est)
            out[pos : pos + take] = self.shifter.process(chunk, self.ratio)
            if self._voices:
                # The new level target starts at the next hop, so the result doesn't depend on block sizes.
                tgt = self._env_target
                ramp = tgt + (self._env - tgt) * self._env_decay ** np.arange(1, take + 1)
                self._env = float(ramp[-1])
                self._env_target = self._env_next
                env = np.concatenate([self._env_delay, ramp])
                env, self._env_delay = env[:take], env[take:]
                chord = sum(v.process(chunk, r) for v, r in zip(self._voices, self.voice_ratios))
                out[pos : pos + take] += self.harmony * env * chord
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
