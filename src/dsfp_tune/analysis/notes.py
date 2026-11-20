"""Converting between frequency (Hz), MIDI note numbers and note names.

MIDI note 69 = A4 = 440 Hz; one unit = one semitone; 100 cents = one semitone.
"""

from __future__ import annotations

import numpy as np

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

__all__ = ["NOTE_NAMES", "hz_to_midi", "midi_to_hz", "note_name", "nearest_note"]


def hz_to_midi(f_hz, a4: float = 440.0):
    """Frequency in Hz to (fractional) MIDI note number. Works on scalars and arrays."""
    return 69.0 + 12.0 * np.log2(np.asarray(f_hz, dtype=np.float64) / a4)


def midi_to_hz(midi, a4: float = 440.0):
    """(Fractional) MIDI note number to Hz."""
    return a4 * 2.0 ** ((np.asarray(midi, dtype=np.float64) - 69.0) / 12.0)


def note_name(midi: int) -> str:
    """MIDI note number to a name like ``'A4'`` or ``'C#3'`` (rounded to the nearest note)."""
    n = int(round(float(midi)))
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def nearest_note(f_hz: float, a4: float = 440.0) -> tuple[str, float]:
    """Nearest equal-tempered note to ``f_hz`` and the offset from it in cents (-50..+50)."""
    m = float(hz_to_midi(f_hz, a4))
    n = round(m)
    return note_name(n), 100.0 * (m - n)
