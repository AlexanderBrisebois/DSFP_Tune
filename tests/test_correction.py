"""Frequency shifter, pitch correction (autotune) and the `tune.py autotune` command."""

import sys
import types
from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import pytest

from dsfp_tune.analysis import yin_track
from dsfp_tune.correction import NoteSnapper, PitchCorrector, correct_pitch
from dsfp_tune.io import save_wav
from dsfp_tune.synthesis import FrequencyShifter, freq_shift
from dsfp_tune.theory import Key, freq_to_midi

SR = 44100


def voice(f_of_t, seconds, sr=SR, harmonics=(0.5, 1, 0.7, 0.5, 0.3, 0.2, 0.1)):
    """Harmonic tone whose fundamental follows f_of_t(t) (Hz)."""
    t = np.arange(int(seconds * sr)) / sr
    phase = 2 * np.pi * np.cumsum(np.broadcast_to(f_of_t(t), t.shape)) / sr
    return 0.15 * sum(a * np.sin((k + 1) * phase) for k, a in enumerate(harmonics))


def held(midi, cents=0.0, vibrato=0.0, seconds=1.5):
    f = 440 * 2 ** ((midi - 69) / 12)
    return voice(lambda t: f * 2 ** ((cents + vibrato * np.sin(2 * np.pi * 5.5 * t)) / 1200), seconds)


def out_midi(x, trim=0.15):
    """Voiced output pitch (MIDI), ignoring the first/last `trim` fraction."""
    tr = yin_track(x, SR)
    m = freq_to_midi(tr.f0[tr.voiced])
    n = int(len(m) * trim)
    return m[n : len(m) - n]


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


# --- FrequencyShifter ----------------------------------------------------------------


def test_shifter_ratio_one_is_exact_delayed_identity():
    rng = np.random.default_rng(0)
    x = rng.standard_normal(20000)
    s = FrequencyShifter(2048)
    out, pos = [], 0
    while pos < len(x):  # random block sizes
        b = int(rng.integers(1, 700))
        out.append(s.process(x[pos : pos + b], 1.0))
        pos += b
    y = np.concatenate(out)
    assert len(y) == len(x)
    np.testing.assert_allclose(y[s.latency :], x[: -s.latency], atol=1e-12)


@pytest.mark.parametrize("semitones", [-3, -1, 0.3, 1, 2, 12])
def test_freq_shift_moves_pitch(semitones):
    ratio = 2 ** (semitones / 12)
    y = freq_shift(held(57, seconds=1.0), ratio)
    got = np.median(out_midi(y))
    assert got == pytest.approx(57 + semitones, abs=0.02)  # within 2 cents


def test_freq_shift_same_length_no_delay():
    x = held(60, seconds=0.5)
    y = freq_shift(x, 1.0)
    assert len(y) == len(x)
    np.testing.assert_allclose(y, x, atol=1e-12)


def test_shifter_rejects_bad_settings():
    with pytest.raises(ValueError):
        FrequencyShifter(1000)  # not a power of two
    with pytest.raises(ValueError):
        FrequencyShifter(1024, overlap=3)
    with pytest.raises(ValueError):
        FrequencyShifter(1024).process(np.zeros(10), ratio=10.0)


# --- NoteSnapper ---------------------------------------------------------------------


def test_snapper_chromatic_and_key():
    assert NoteSnapper().nearest(57.3) == 57
    assert NoteSnapper().nearest(60.8) == 61
    c_major = NoteSnapper("C major")
    assert c_major.nearest(60.8) == 60  # C#4-20c -> C4 (D4 is further)
    assert c_major.nearest(61.4) == 62
    assert NoteSnapper(Key("A", "minor_pentatonic")).nearest(65.4) == 64  # F not in scale -> E


def test_snapper_hysteresis():
    s = NoteSnapper(hysteresis=0.15)
    assert s.update(57.45) == 57
    assert s.update(57.55) == 57  # just past the midpoint: keep A3
    assert s.update(57.70) == 58  # clearly closer to A#3: switch
    s.reset()
    assert s.update(57.55) == 58


# --- PitchCorrector / correct_pitch --------------------------------------------------------------


@pytest.mark.parametrize("cents", [-40, -15, 25, 45])
def test_corrects_out_of_tune_note(cents):
    r = correct_pitch(held(57, cents=cents), SR)
    assert np.median(out_midi(r.samples)) == pytest.approx(57, abs=0.03)
    assert np.nanmedian(r.target_midi) == 57
    assert len(r.samples) == int(1.5 * SR)


def test_key_changes_the_target():
    x = held(61, cents=-20)  # C#4 -20c
    assert np.median(out_midi(correct_pitch(x, SR, retune_ms=0).samples)) == pytest.approx(61, abs=0.03)
    assert np.median(out_midi(correct_pitch(x, SR, key="C major", retune_ms=0).samples)) == pytest.approx(60, abs=0.03)


def test_retune_speed_controls_vibrato():
    x = held(57, cents=10, vibrato=30, seconds=2.0)
    spread = lambda m: np.percentile(m, 95) - np.percentile(m, 5)  # noqa: E731
    fast = out_midi(correct_pitch(x, SR, retune_ms=0).samples)
    slow = out_midi(correct_pitch(x, SR, retune_ms=300).samples)
    assert np.median(fast) == pytest.approx(57, abs=0.05) and np.median(slow) == pytest.approx(57, abs=0.05)
    assert spread(fast) < 0.3 < spread(slow)  # fast flattens vibrato, slow keeps it


def test_vibrato_across_midpoint_keeps_one_target():
    # -40c with +/-25c vibrato dips below the B3/C4 midpoint; the target must stay C4.
    r = correct_pitch(held(60, cents=-40, vibrato=25, seconds=2.0), SR, key="A minor")
    targets = r.target_midi[~np.isnan(r.target_midi)]
    assert set(np.unique(targets)) == {60}


def test_fast_note_changes_follow():
    notes = [57, 59, 60, 62, 64]
    x = np.concatenate([held(n, cents=20, seconds=0.15) for n in notes])
    y = correct_pitch(x, SR, key="A minor", retune_ms=0).samples
    tr = yin_track(y, SR)
    m = freq_to_midi(tr.f0)
    for i, n in enumerate(notes):
        seg = (tr.times > i * 0.15 + 0.04) & (tr.times < (i + 1) * 0.15 - 0.02)
        assert np.nanmedian(m[seg]) == pytest.approx(n, abs=0.05)


def test_unpitched_audio_passes_through_unchanged():
    rng = np.random.default_rng(3)
    noise = 0.1 * rng.standard_normal(SR)
    r = correct_pitch(noise, SR)
    assert not r.voiced.any()
    np.testing.assert_allclose(r.samples, noise, atol=1e-6)


def test_large_errors_are_not_corrected():
    # E4 in F# major pentatonic (F# G# A# C# D#): nearest in-key note is D#4, 0.9 semitones away,
    # more than max_correction, so the note is left alone.
    x = held(64, cents=-10)
    r = correct_pitch(x, SR, key=Key("F#", "major_pentatonic"), max_correction=0.5)
    assert np.median(out_midi(r.samples)) == pytest.approx(63.9, abs=0.05)


def test_streaming_matches_offline():
    x = held(57, cents=30, seconds=1.0)
    pc = PitchCorrector(SR)
    rng = np.random.default_rng(1)
    out, pos = [], 0
    while pos < len(x):
        b = int(rng.integers(32, 1024))
        out.append(pc.process(x[pos : pos + b]))
        pos += b
    streamed = np.concatenate(out)
    offline = correct_pitch(x, SR).samples
    lat = pc.latency
    np.testing.assert_allclose(streamed[lat:], offline[: len(x) - lat], atol=1e-5)


def test_history_and_latest():
    pc = PitchCorrector(SR, keep_history=True)
    pc.process(held(57, cents=30, seconds=0.5))
    assert pc.latest is pc.history[-1]
    assert pc.latest.voiced and pc.latest.target_midi == 57
    assert pc.latest.ratio == pytest.approx(2 ** (pc.latest.correction / 12))
    with pytest.raises(ValueError):
        PitchCorrector(SR, retune_ms=-1)


def test_plot_correction():
    from dsfp_tune.plotting import plot_correction

    x = np.concatenate([held(57, 30, seconds=0.5), np.zeros(SR // 5), held(60, -30, seconds=0.5)])
    fig = plot_correction(x, correct_pitch(x, SR, key="A minor"), title="t")
    assert len(fig.axes) == 2
    assert "A minor" in fig.axes[0].get_title()
    plot_correction(np.zeros(SR // 2), correct_pitch(np.zeros(SR // 2), SR))  # nothing voiced


# --- real-time duplex + CLI (fake sounddevice) -------------------------------------------------------------


class FakeStream:
    last = None

    def __init__(self, samplerate, blocksize, device, channels, dtype, latency, callback):
        self.blocksize, self.channels, self.callback = blocksize, channels, callback
        self.latency = (0.01, 0.02)
        self.active = True
        FakeStream.last = self

    def start(self):
        pass

    def run_blocks(self, signal):
        out = []
        for i in range(0, len(signal) - self.blocksize + 1, self.blocksize):
            o = np.zeros((self.blocksize, self.channels[1]), np.float32)
            self.callback(signal[i : i + self.blocksize, None].astype(np.float32), o, self.blocksize, None, None)
            out.append(o)
        return np.concatenate(out)

    def stop(self):
        self.active = False

    def close(self):
        pass


@pytest.fixture
def fake_sd(monkeypatch):
    sd = types.ModuleType("sounddevice")
    sd.default = SimpleNamespace(device=[0, 1])
    devices = {0: dict(name="Built-in Mic", max_input_channels=1, max_output_channels=0),
               1: dict(name="MacBook Pro Speakers", max_input_channels=0, max_output_channels=2),
               2: dict(name="Headphones", max_input_channels=0, max_output_channels=2)}
    sd.query_devices = lambda device=None, kind=None: devices[1 if device is None else device]
    sd.Stream = FakeStream
    sd.CallbackAbort = type("CallbackAbort", (Exception,), {})
    sd.played = []
    sd.play = lambda x, samplerate, device=None: sd.played.append(len(x))
    sd.wait = lambda: None
    sd.recorded = held(57, cents=35, seconds=1.0).astype(np.float32)
    sd.rec = lambda n, samplerate, channels, dtype, device: sd.recorded[:n, None].copy()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def test_duplex_stream_runs_corrector(fake_sd):
    from dsfp_tune.io.microphone import DuplexStream

    pc = PitchCorrector(SR, n_fft=1024)
    with DuplexStream(pc.process, SR, blocksize=256, output_device=2) as d:
        out = FakeStream.last.run_blocks(held(57, cents=35, seconds=1.0))
        assert d.total_latency == pytest.approx(0.03)
    assert out.shape[1] == 2 and np.allclose(out[:, 0], out[:, 1])  # mono copied to both ears
    assert np.median(out_midi(out[:, 0].astype(np.float64))) == pytest.approx(57, abs=0.05)
    assert d.xruns == 0 and d.error is None


def test_duplex_stream_reports_processing_errors(fake_sd):
    from dsfp_tune.io.microphone import DuplexStream

    def broken(x):
        raise RuntimeError("boom")

    with DuplexStream(broken, SR) as d:
        with pytest.raises(fake_sd.CallbackAbort):
            FakeStream.last.run_blocks(np.zeros(512))
    assert isinstance(d.error, RuntimeError)


def test_autotune_cli_file(tmp_path, capsys, fake_sd):
    from tune import main

    wav = save_wav(tmp_path / "take.wav", held(57, cents=35, seconds=1.0), SR)
    out = tmp_path / "tuned.wav"
    main(["autotune", str(wav), "--key", "A minor", "--out", str(out), "--no-plot"])
    text = capsys.readouterr().out
    assert out.exists() and "A3" in text and "A minor" in text
    assert fake_sd.played == [SR, SR]  # original, then corrected
    from dsfp_tune.io import load_audio

    assert np.median(out_midi(load_audio(out).samples.astype(np.float64))) == pytest.approx(57, abs=0.05)


def test_autotune_cli_records_and_plots(tmp_path, capsys, fake_sd, monkeypatch):
    from tune import main

    monkeypatch.chdir(tmp_path)
    shown = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(plt.gcf()))
    main(["autotune", "--seconds", "1", "--no-play"])
    assert len(list((tmp_path / "data/audio").glob("mic_*.wav"))) == 1
    assert len(list((tmp_path / "data/output").glob("mic_*_tuned.wav"))) == 1
    assert len(shown) == 1 and len(shown[0].axes) == 2


def test_autotune_cli_bad_key(fake_sd):
    from tune import main

    with pytest.raises(SystemExit, match="bad --key"):
        main(["autotune", "--key", "H minor", "--no-play", "--no-plot"])


def test_autotune_live_refuses_speakers(fake_sd):
    from tune import main

    with pytest.raises(SystemExit, match="headphones"):
        main(["autotune", "--live"])


# --- amount / release / validation (advanced settings) --------------------------------------------


@pytest.mark.parametrize("amount, expected_cents", [(1.0, 0), (0.75, 10), (0.5, 20), (0.0, 40)])
def test_amount_scales_the_correction(amount, expected_cents):
    y = correct_pitch(held(57, cents=40), SR, amount=amount).samples
    assert 100 * (np.median(out_midi(y)) - 57) == pytest.approx(expected_cents, abs=1.5)


def test_release_controls_how_fast_correction_lets_go():
    x = np.concatenate([held(57, cents=40, seconds=1.0), np.zeros(int(0.6 * SR))])

    def let_go_ms(**kw):
        pc = PitchCorrector(SR, retune_ms=25, keep_history=True, **kw)
        pc.process(x)
        t = np.array([h.time for h in pc.history])
        c = np.abs([h.correction for h in pc.history]) * 100
        after = t > 1.0
        return 1000 * (t[after][np.argmax(c[after] < 20)] - 1.0)

    fast, default, slow = let_go_ms(release_ms=0), let_go_ms(), let_go_ms(release_ms=200)
    assert fast < default < slow
    assert slow > 150 and fast < 80
    assert let_go_ms(release_ms=25) == pytest.approx(default)  # default release = retune_ms


@pytest.mark.parametrize(
    "kwargs",
    [dict(amount=1.5), dict(amount=-0.1), dict(release_ms=-1), dict(hold_ms=-1), dict(note_window_ms=-5),
     dict(hysteresis=-0.1), dict(note_jump=0), dict(max_correction=0)],
)
def test_corrector_rejects_bad_settings(kwargs):
    with pytest.raises(ValueError):
        PitchCorrector(SR, **kwargs)


def test_autotune_cli_amount_and_release(tmp_path, fake_sd):
    from tune import main

    wav = save_wav(tmp_path / "take.wav", held(57, cents=40, seconds=1.0), SR)
    out = tmp_path / "half.wav"
    main(["autotune", str(wav), "--amount", "0.5", "--release-ms", "100", "--out", str(out), "--no-plot", "--no-play"])
    from dsfp_tune.io import load_audio

    assert 100 * (np.median(out_midi(load_audio(out).samples.astype(np.float64))) - 57) == pytest.approx(20, abs=2)
    with pytest.raises(SystemExit, match="amount"):
        main(["autotune", str(wav), "--amount", "2", "--no-plot", "--no-play"])
