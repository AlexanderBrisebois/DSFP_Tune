"""Notes and frequencies in 12-tone equal temperament (12-TET).

Notes are identified by MIDI number: C4 (middle C) = 60, A4 = 69.
Frequency:  f = a4 * 2 ** ((midi - 69) / 12),  with a4 = 440 Hz by default.
"""

from __future__ import annotations

import re

import numpy as np

A4_MIDI = 69
DEFAULT_A4 = 440.0

NOTE_NAMES_SHARP = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
NOTE_NAMES_FLAT = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")

_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]*)\s*(-?\d+)?\s*$")


def parse_pitch_class(name: str) -> int:
    """'C' -> 0, 'C#'/'Db' -> 1, ..., 'B' -> 11. Octave digits, if present, are ignored."""
    m = _NOTE_RE.match(name)
    if not m:
        raise ValueError(f"Not a note name: {name!r}")
    letter, acc, _ = m.groups()
    shift = acc.count("#") + acc.count("♯") - acc.count("b") - acc.count("♭")
    return (_LETTER_PC[letter.upper()] + shift) % 12


def name_to_midi(name: str) -> int:
    """'A4' -> 69, 'C4' -> 60, 'Bb3' -> 58. Octave is required."""
    m = _NOTE_RE.match(name)
    if not m or m.group(3) is None:
        raise ValueError(f"Expected a note with octave like 'A4' or 'C#3', got {name!r}")
    letter, acc, octave = m.groups()
    shift = acc.count("#") + acc.count("♯") - acc.count("b") - acc.count("♭")
    return 12 * (int(octave) + 1) + _LETTER_PC[letter.upper()] + shift


def midi_to_name(midi: int, flats: bool = False) -> str:
    """69 -> 'A4'. Uses sharps unless ``flats=True``."""
    midi = int(round(midi))
    names = NOTE_NAMES_FLAT if flats else NOTE_NAMES_SHARP
    return f"{names[midi % 12]}{midi // 12 - 1}"


def midi_to_freq(midi, a4: float = DEFAULT_A4):
    """MIDI number (int, float or array) -> frequency in Hz."""
    return a4 * 2.0 ** ((np.asarray(midi, dtype=np.float64) - A4_MIDI) / 12.0)


def freq_to_midi(freq, a4: float = DEFAULT_A4):
    """Frequency in Hz -> fractional MIDI number. Non-positive frequencies give NaN."""
    f = np.asarray(freq, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = A4_MIDI + 12.0 * np.log2(f / a4)
    return np.where(f > 0, m, np.nan)


def nearest_note(freq: float, a4: float = DEFAULT_A4, flats: bool = False) -> tuple[str, float]:
    """Nearest 12-TET note to ``freq`` and the offset from it in cents (-50..+50).

    ``nearest_note(445.0)`` -> ``('A4', 19.56...)``: A4, 19.6 cents sharp.
    """
    m = float(freq_to_midi(freq, a4))
    if np.isnan(m):
        raise ValueError(f"frequency must be positive, got {freq!r}")
    n = round(m)
    return midi_to_name(n, flats=flats), 100.0 * (m - n)


def note_table(low: int = 0, high: int = 127, a4: float = DEFAULT_A4) -> list[dict]:
    """Rows of {midi, name, name_flat, pitch_class, octave, frequency_hz} for MIDI low..high."""
    rows = []
    for m in range(low, high + 1):
        rows.append({
            "midi": m,
            "name": midi_to_name(m),
            "name_flat": midi_to_name(m, flats=True),
            "pitch_class": m % 12,
            "octave": m // 12 - 1,
            "frequency_hz": round(float(midi_to_freq(m, a4)), 4),
        })
    return rows
