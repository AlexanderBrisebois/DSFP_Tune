"""Chord harmony: the snapped note plus the key's triad on it (PitchCorrector(harmony=...))."""

import numpy as np
import pytest

from dsfp_tune.correction import PitchCorrector, correct_pitch
from dsfp_tune.io import save_wav
from dsfp_tune.theory import Key, diatonic_triad, midi_to_freq
from dsfp_tune.web import SnapSettings
from tests.test_correction import SR, fake_sd, held  # noqa: F401  (fake_sd is a fixture)


def level_at(x, freq, sr=SR):
    """Spectral magnitude near ``freq`` (max over +-1.5 % so a few cents of error don't matter)."""
    mid = x[len(x) // 4 : 3 * len(x) // 4]
    spec = np.abs(np.fft.rfft(mid * np.hanning(len(mid))))
    f = np.fft.rfftfreq(len(mid), 1 / sr)
    return spec[(f > freq * 0.985) & (f < freq * 1.015)].max()


# --- diatonic_triad -----------------------------------------------------------------------------


@pytest.mark.parametrize("key, root, chord", [
    (Key("C"), 57, (57, 60, 64)),          # A in C major -> A minor: A C E
    (Key("C"), 60, (60, 64, 67)),          # C major: C E G
    (Key("C"), 67, (67, 71, 74)),          # G major: G B D
    (Key("C"), 71, (71, 74, 77)),          # B diminished: B D F
    (Key("A", "minor"), 64, (64, 67, 71)), # E minor in A natural minor
    (Key("A", "harmonic_minor"), 64, (64, 68, 71)),  # E major (G#) in A harmonic minor
    (Key("F#"), 66, (66, 70, 73)),         # F# major: F# A# C#
])
def test_diatonic_triad(key, root, chord):
    assert diatonic_triad(key, root) == chord


def test_diatonic_triad_errors():
    with pytest.raises(ValueError, match="7-note"):
        diatonic_triad(Key("A", "minor_pentatonic"), 57)
    with pytest.raises(ValueError, match="not in"):
        diatonic_triad(Key("C"), 61)   # C# is not in C major


# --- PitchCorrector(harmony=...) ----------------------------------------------------------------


def test_harmony_needs_a_seven_note_key():
    with pytest.raises(ValueError, match="needs a key"):
        PitchCorrector(SR, harmony=0.5)
    with pytest.raises(ValueError, match="7-note"):
        PitchCorrector(SR, key="C chromatic", harmony=0.5)
    with pytest.raises(ValueError, match=">= 0"):
        PitchCorrector(SR, key="C major", harmony=-1)


def test_sung_a_in_c_major_adds_a_minor_chord():
    x = held(57, cents=30)                  # A3, 30 cents sharp
    lead = correct_pitch(x, SR, key="C major")
    chord = correct_pitch(x, SR, key="C major", harmony=0.5)
    a, c, e = (midi_to_freq(m) for m in (57, 60, 64))
    # Lead alone: almost nothing at C4 / E4 (not harmonics of A3).
    assert level_at(lead.samples, c) < 0.05 * level_at(lead.samples, a)
    assert level_at(lead.samples, e) < 0.05 * level_at(lead.samples, a)
    # With harmony: the snapped root stays, and C4 and E4 appear at about half the root's level.
    for f in (c, e):
        assert 0.3 < level_at(chord.samples, f) / level_at(chord.samples, a) < 0.8
    # Not-in-chord notes nearby stay quiet (e.g. C#4, D4, F4).
    for m in (61, 62, 65):
        assert level_at(chord.samples, midi_to_freq(m)) < 0.1 * level_at(chord.samples, a)


def test_harmony_zero_is_unchanged():
    x = held(57, cents=30, seconds=1.0)
    np.testing.assert_array_equal(correct_pitch(x, SR, key="C major").samples,
                                  correct_pitch(x, SR, key="C major", harmony=0.0).samples)


def test_chord_follows_the_snapped_note():
    x = np.concatenate([held(57, cents=20, seconds=0.8), held(67, cents=-25, seconds=0.8)])  # A3 then G4
    pc = PitchCorrector(SR, key="C major", harmony=0.5, keep_history=True)
    pc.process(x)
    chords = [f.chord for f in pc.history if f.chord]
    assert dict.fromkeys(chords).keys() == {(57, 60, 64): 0, (67, 71, 74): 0}.keys()  # Am, then G
    assert all(f.chord[0] == f.target_midi for f in pc.history if f.chord)  # root = snapped note


def test_no_chord_without_pitch():
    rng = np.random.default_rng(1)
    noise = 0.05 * rng.standard_normal(SR)
    pc = PitchCorrector(SR, key="C major", harmony=0.5, keep_history=True)
    y = pc.process(np.concatenate([noise, np.zeros(pc.latency)]))[pc.latency :]
    assert not any(f.chord for f in pc.history)
    np.testing.assert_allclose(y, noise, atol=1e-9)   # chord voices silent, lead passes through


def test_streaming_with_harmony_matches_offline():
    x = held(57, cents=30, seconds=1.0)
    off = correct_pitch(x, SR, key="C major", harmony=0.5).samples
    pc = PitchCorrector(SR, key="C major", harmony=0.5)
    blocks = [pc.process(x[i : i + 300]) for i in range(0, len(x), 300)]
    blocks.append(pc.process(np.zeros(pc.latency)))
    stream = np.concatenate(blocks)[pc.latency : pc.latency + len(x)]
    np.testing.assert_allclose(stream, off, atol=1e-5)


# --- CLI and dashboard setting --------------------------------------------------------------------


def test_cli_harmony(tmp_path, capsys, fake_sd):
    from tune import main

    wav = save_wav(tmp_path / "take.wav", held(57, cents=35, seconds=1.0), SR)
    out = tmp_path / "tuned.wav"
    main(["autotune", str(wav), "--key", "C major", "--harmony", "0.5", "--out", str(out),
          "--no-plot", "--no-play"])
    assert out.exists() and "chord harmony 0.5" in capsys.readouterr().out


@pytest.mark.parametrize("args, msg", [
    (["--harmony", "0.5"], "needs --key"),
    (["--key", "A minor_pentatonic", "--harmony", "0.5"], "7-note"),
    (["--key", "C", "--harmony", "-1"], ">= 0"),
])
def test_cli_harmony_errors(fake_sd, args, msg):
    from tune import main

    with pytest.raises(SystemExit, match=msg):
        main(["autotune", "--no-play", "--no-plot", *args])


def test_dashboard_setting():
    assert SnapSettings().harmony == 0.0
    assert SnapSettings.from_dict({"mode": "key", "tonic": 0, "harmony": 0.4}).harmony == 0.4
    with pytest.raises(ValueError, match="harmony"):
        SnapSettings.from_dict({"harmony": 2})


def test_dashboard_autotune_with_harmony():
    import io
    import wave

    from dsfp_tune.web.server import autotune_samples

    x = held(57, cents=30, seconds=1.0)
    wav, summary = autotune_samples(x, SR, SnapSettings("key", 0, "major", 0.5))
    assert summary["harmony"] == 0.5
    with wave.open(io.BytesIO(wav)) as w:
        y = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767
    assert level_at(y, midi_to_freq(60)) > 0.2 * level_at(y, midi_to_freq(57))   # C4 added under A3
    # Ignored where chords don't apply: chromatic mode, or a 5-note scale.
    assert autotune_samples(x, SR, SnapSettings("chromatic", 0, "major", 0.5))[1]["harmony"] == 0.0
    assert autotune_samples(x, SR, SnapSettings("key", 9, "minor_pentatonic", 0.5))[1]["harmony"] == 0.0


def test_chord_held_through_pitch_dropouts_but_not_silence():
    rng = np.random.default_rng(2)
    note = held(57, cents=20, seconds=0.6)
    burst = np.std(note) * rng.standard_normal(int(0.2 * SR))   # loud, but no pitch (YIN dropout)
    x = np.concatenate([note, burst, note, np.zeros(int(0.5 * SR))])
    pc = PitchCorrector(SR, key="C major", harmony=0.5, keep_history=True)
    pc.process(x)
    t = np.array([f.time for f in pc.history])
    on = np.array([bool(f.chord) for f in pc.history])
    voiced = np.array([f.voiced for f in pc.history])
    gap = (t > 0.65) & (t < 0.75)
    assert voiced[gap].all() and on[gap].all()            # pitch carried over, chord kept
    assert not on[t > 1.55].any()                         # singer stopped: chord gone
    pc = PitchCorrector(SR, key="C major", harmony=0.5, pitch_hold_ms=0, keep_history=True)
    pc.process(x)
    assert not any(f.chord for f, tt in zip(pc.history, t) if 0.72 < tt < 0.78)   # without the pitch hold
