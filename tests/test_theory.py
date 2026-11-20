import pytest

from dsfp_tune.theory import (
    Key, all_keys, freq_to_midi, midi_to_freq, midi_to_name, name_to_midi, note_table,
    parse_key, parse_pitch_class,
)


@pytest.mark.parametrize("name,midi", [("A4", 69), ("C4", 60), ("C-1", 0), ("G9", 127),
                                       ("Bb3", 58), ("A#3", 58), ("Cb4", 59), ("E#4", 65)])
def test_name_to_midi(name, midi):
    assert name_to_midi(name) == midi


def test_frequencies():
    assert midi_to_freq(69) == pytest.approx(440.0)
    assert midi_to_freq(60) == pytest.approx(261.6256, abs=1e-3)
    assert midi_to_freq(81) == pytest.approx(880.0)
    assert midi_to_freq(69, a4=432) == pytest.approx(432.0)
    assert freq_to_midi(440 * 2 ** (1 / 12)) == pytest.approx(70)


def test_names_round_trip():
    for m in range(128):
        assert name_to_midi(midi_to_name(m)) == m
        assert name_to_midi(midi_to_name(m, flats=True)) == m


def test_note_table():
    rows = note_table()
    assert len(rows) == 128
    a4 = rows[69]
    assert a4["name"] == "A4" and a4["frequency_hz"] == 440.0 and a4["octave"] == 4


@pytest.mark.parametrize("key,notes", [
    ("C major", ["C", "D", "E", "F", "G", "A", "B"]),
    ("A minor", ["A", "B", "C", "D", "E", "F", "G"]),
    ("G major", ["G", "A", "B", "C", "D", "E", "F#"]),
    ("F major", ["F", "G", "A", "Bb", "C", "D", "E"]),
    ("D dorian", ["D", "E", "F", "G", "A", "B", "C"]),
    ("A harmonic minor", ["A", "B", "C", "D", "E", "F", "G#"]),
    ("E minor pentatonic", ["E", "G", "A", "B", "D"]),
    ("Eb minor", ["Eb", "F", "Gb", "Ab", "Bb", "Cb", "Db"]),
    ("Gb major", ["Gb", "Ab", "Bb", "Cb", "Db", "Eb", "F"]),
    ("B major", ["B", "C#", "D#", "E", "F#", "G#", "A#"]),
])
def test_key_notes(key, notes):
    assert list(parse_key(key).note_names) == notes


def test_parse_key_shortcuts():
    assert parse_key("Am") == Key("A", "minor")
    assert parse_key("C") == Key("C", "major")
    assert parse_key("Db major") == Key("C#", "major")
    assert parse_pitch_class("Db") == parse_pitch_class("C#") == 1


def test_relative_keys_share_notes():
    assert set(Key("C").pitch_classes) == set(Key("A", "minor").pitch_classes)


def test_notes_in_range():
    notes = Key("C").notes_in_range(60, 72)
    assert [n["name"] for n in notes] == ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]
    assert notes[5]["frequency_hz"] == 440.0 and notes[5]["degree"] == 6


def test_all_keys_count():
    assert len(all_keys()) == 12 * 14
    assert len({k.name for k in all_keys()}) == 12 * 14


def test_unknown_scale():
    with pytest.raises(ValueError):
        Key("C", "nonsense")
