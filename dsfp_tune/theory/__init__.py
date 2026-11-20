"""Music theory: notes, frequencies, and keys/scales."""

from dsfp_tune.theory.notes import (
    NOTE_NAMES_SHARP,
    NOTE_NAMES_FLAT,
    freq_to_midi,
    midi_to_freq,
    midi_to_name,
    name_to_midi,
    nearest_note,
    note_table,
    parse_pitch_class,
)
from dsfp_tune.theory.keys import SCALES, Key, parse_key, all_keys, diatonic_triad

__all__ = [
    "NOTE_NAMES_SHARP", "NOTE_NAMES_FLAT", "freq_to_midi", "midi_to_freq", "midi_to_name",
    "name_to_midi", "nearest_note", "note_table", "parse_pitch_class", "SCALES", "Key", "parse_key",
    "all_keys", "diatonic_triad",
]
