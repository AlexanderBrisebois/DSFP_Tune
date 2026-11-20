"""Keys and scales.

A key is a tonic pitch class plus a scale (a set of semitone offsets from the
tonic). ``Key("A", "minor").pitch_classes`` -> (9, 11, 0, 2, 4, 5, 7).
"""

from __future__ import annotations

from dataclasses import dataclass

from dsfp_tune.theory.notes import (
    DEFAULT_A4, NOTE_NAMES_FLAT, NOTE_NAMES_SHARP, midi_to_freq, midi_to_name, parse_pitch_class,
)

_LETTERS = "CDEFGAB"
_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Semitone offsets from the tonic.
SCALES: dict[str, tuple[int, ...]] = {
    "major":            (0, 2, 4, 5, 7, 9, 11),
    "minor":            (0, 2, 3, 5, 7, 8, 10),   # natural minor (aeolian)
    "harmonic_minor":   (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor":    (0, 2, 3, 5, 7, 9, 11),   # ascending form
    "ionian":           (0, 2, 4, 5, 7, 9, 11),
    "dorian":           (0, 2, 3, 5, 7, 9, 10),
    "phrygian":         (0, 1, 3, 5, 7, 8, 10),
    "lydian":           (0, 2, 4, 6, 7, 9, 11),
    "mixolydian":       (0, 2, 4, 5, 7, 9, 10),
    "aeolian":          (0, 2, 3, 5, 7, 8, 10),
    "locrian":          (0, 1, 3, 5, 6, 8, 10),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "blues":            (0, 3, 5, 6, 7, 10),
    "chromatic":        tuple(range(12)),
}

_ALIASES = {"maj": "major", "min": "minor", "natural_minor": "minor", "m": "minor"}

# Tonics conventionally spelled with flats (major keys, and minor keys by their own tonic).
_FLAT_MAJOR_TONICS = {5, 10, 3, 8, 1, 6}      # F Bb Eb Ab Db Gb
_FLAT_MINOR_TONICS = {2, 7, 0, 5, 10, 3}      # D G C F Bb Eb minor



def _scale_name(name: str) -> str:
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    key = _ALIASES.get(key, key)
    if key not in SCALES:
        raise ValueError(f"Unknown scale {name!r}. Known: {', '.join(SCALES)}")
    return key


@dataclass(frozen=True)
class Key:
    tonic: int            # pitch class 0..11
    scale: str = "major"

    def __init__(self, tonic: int | str, scale: str = "major"):
        pc = parse_pitch_class(tonic) if isinstance(tonic, str) else int(tonic) % 12
        object.__setattr__(self, "tonic", pc)
        object.__setattr__(self, "scale", _scale_name(scale))

    @property
    def uses_flats(self) -> bool:
        if self.scale in ("minor", "aeolian", "harmonic_minor", "melodic_minor",
                          "minor_pentatonic", "blues", "dorian", "phrygian", "locrian"):
            return self.tonic in _FLAT_MINOR_TONICS
        return self.tonic in _FLAT_MAJOR_TONICS

    @property
    def intervals(self) -> tuple[int, ...]:
        return SCALES[self.scale]

    @property
    def pitch_classes(self) -> tuple[int, ...]:
        """Pitch classes in scale order starting from the tonic."""
        return tuple((self.tonic + i) % 12 for i in self.intervals)

    @property
    def note_names(self) -> tuple[str, ...]:
        """Note names in scale order.

        7-note scales are spelled with each letter once (Eb minor -> ..., Cb, Db);
        other scales use the key's sharp/flat preference.
        """
        names = NOTE_NAMES_FLAT if self.uses_flats else NOTE_NAMES_SHARP
        if len(self.intervals) != 7:
            return tuple(names[pc] for pc in self.pitch_classes)
        start = _LETTERS.index(names[self.tonic][0])
        out = []
        for i, pc in enumerate(self.pitch_classes):
            letter = _LETTERS[(start + i) % 7]
            diff = (pc - _LETTER_PC[letter] + 6) % 12 - 6     # -2..2 in practice
            out.append(letter + ("#" * diff if diff > 0 else "b" * -diff))
        return tuple(out)

    @property
    def name(self) -> str:
        names = NOTE_NAMES_FLAT if self.uses_flats else NOTE_NAMES_SHARP
        return f"{names[self.tonic]} {self.scale.replace('_', ' ')}"

    def contains(self, pitch_class: int) -> bool:
        return pitch_class % 12 in self.pitch_classes

    def notes_in_range(self, low_midi: int = 21, high_midi: int = 108, a4: float = DEFAULT_A4) -> list[dict]:
        """Every in-key note between two MIDI numbers (default: piano range A0..C8)."""
        pcs = set(self.pitch_classes)
        return [
            {"midi": m, "name": midi_to_name(m, self.uses_flats),
             "degree": self.pitch_classes.index(m % 12) + 1,
             "frequency_hz": round(float(midi_to_freq(m, a4)), 4)}
            for m in range(low_midi, high_midi + 1) if m % 12 in pcs
        ]

    def __str__(self) -> str:
        return self.name


def parse_key(text: str) -> Key:
    """'A minor', 'F# major', 'Bb dorian', 'C', 'Am', 'Ebm' -> Key."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        raise ValueError("Empty key")
    tonic = parts[0]
    if len(parts) == 1:
        if tonic.endswith("m") and len(tonic) > 1:
            return Key(tonic[:-1], "minor")
        return Key(tonic, "major")
    return Key(tonic, parts[1])


def diatonic_triad(key: Key, root_midi: int) -> tuple[int, int, int]:
    """The triad built on ``root_midi`` from the notes of ``key`` (root, third, fifth as MIDI).

    Stacks every other scale note above the root, so the chord quality comes from the key:
    C major + A -> (A, C, E) = A minor; C major + G -> (G, B, D); C major + B -> (B, D, F).
    Needs a 7-note (diatonic) scale and a root that is in the key.
    """
    if len(key.intervals) != 7:
        raise ValueError(f"diatonic triads need a 7-note scale; {key.name} has {len(key.intervals)} notes")
    root_midi = int(root_midi)
    pcs = key.pitch_classes
    if root_midi % 12 not in pcs:
        raise ValueError(f"MIDI note {root_midi} is not in {key.name}")
    i = pcs.index(root_midi % 12)
    third = root_midi + (pcs[(i + 2) % 7] - pcs[i]) % 12
    fifth = root_midi + (pcs[(i + 4) % 7] - pcs[i]) % 12
    return root_midi, third, fifth


def all_keys(scales: tuple[str, ...] | None = None) -> list[Key]:
    """Every tonic (12) x every scale (default: all in SCALES except chromatic)."""
    scales = scales or tuple(s for s in SCALES if s != "chromatic")
    return [Key(t, s) for s in scales for t in range(12)]
