import wave

import matplotlib.pyplot as plt
import numpy as np
import pytest

from dsfp_tune.analysis import yin_track
from dsfp_tune.io import load_audio, save_wav
from dsfp_tune.io.audio_io import _load_wave
from dsfp_tune.plotting import LivePitchPlot, cents_from_held_note, note_ticks, plot_pitch, spectrogram

SR = 44100


def sine(f, seconds, sr=SR, amp=0.5):
    return (amp * np.sin(2 * np.pi * f * np.arange(int(seconds * sr)) / sr)).astype(np.float32)


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def test_note_ticks_density():
    labelled, notes = note_ticks(55.2, 66.8)  # narrow: every semitone
    assert notes == list(range(56, 67)) and labelled == notes
    labelled, _ = note_ticks(45, 70)  # medium: naturals only
    assert 61 not in labelled and 60 in labelled and 62 in labelled
    labelled, _ = note_ticks(40, 80)  # wide: C E G
    assert {n % 12 for n in labelled} == {0, 4, 7}
    labelled, _ = note_ticks(20, 100)  # very wide: C only
    assert {n % 12 for n in labelled} == {0}


def test_cents_from_held_note_follows_vibrato_without_wrapping():
    frame_rate = 172.0
    t = np.arange(int(2 * frame_rate)) / frame_rate
    true_cents = -20 + 40 * np.sin(2 * np.pi * 5.5 * t)  # crosses -50 -> would wrap
    f0 = 246.94 * 2 ** (true_cents / 1200)  # B3
    f0[100:120] = np.nan  # an unvoiced gap
    c = cents_from_held_note(f0, frame_rate)
    ok = ~np.isnan(f0)
    np.testing.assert_allclose(c[ok], true_cents[ok], atol=0.5)
    assert np.isnan(c[~ok]).all()


def test_spectrogram_peak():
    t, f, s = spectrogram(sine(440.0, 0.5), SR)
    assert s.shape == (len(f), len(t))
    assert f.max() <= 4000
    assert abs(f[np.argmax(s[:, len(t) // 2])] - 440) < SR / 4096


def test_plot_pitch_draws_track():
    x = np.concatenate([sine(220.0, 0.4), np.zeros(SR // 4, np.float32), sine(330.0, 0.4)])
    track = yin_track(x, SR)
    fig = plot_pitch(x, SR, track=track, title="test")
    ax_w, ax_s, ax_c = fig.axes[:3]
    f0_line = ax_s.get_lines()[0]
    np.testing.assert_array_equal(f0_line.get_ydata(), track.f0)
    labels = [t.get_text() for t in ax_s.get_yticklabels()]
    assert "C4" in labels and all(l[0] in "CEG" for l in labels)
    assert ax_s.get_yscale() == "log"


def test_plot_pitch_silence_and_long_signal():
    plot_pitch(np.zeros(SR), SR)  # nothing voiced: must not crash
    plot_pitch(sine(150.0, 6.0), SR)  # long: envelope waveform path


def test_live_plot_follows_voice():
    view = LivePitchPlot(SR, window_seconds=4.0)
    view.build_figure()
    for i in range(0, SR // 2, 256):
        view.push(sine(220.0, 0.5)[i : i + 256])
    view.redraw()
    t, midi = view.data()
    assert t[-1] == 0 and t[0] < 0
    assert np.nanmedian(midi) == pytest.approx(57, abs=0.1)  # A3
    assert view.readout.get_text().startswith("A3")
    lo, hi = view.ax.get_ylim()
    assert lo < 57 < hi

    view.push(sine(880.0, 1.0))  # jump up two octaves: axis must follow
    view.redraw()
    lo, hi = view.ax.get_ylim()
    assert lo < 81 < hi
    assert view.readout.get_text().startswith("A5")


def test_live_plot_window_is_bounded_and_warns_on_silence():
    view = LivePitchPlot(SR, window_seconds=1.0)
    view.build_figure()
    view.push(np.zeros(3 * SR, np.float32))
    view.redraw()
    t, midi = view.data()
    assert t[0] >= -1.01 and np.isnan(midi).all()
    assert view.readout.get_text() == "—"
    assert "Microphone" in view.status.get_text()


def test_live_plot_tick_reads_microphone():
    class FakeMic:
        overflows = 3

        def __init__(self):
            self.chunks = [sine(330.0, 0.3), np.zeros(0, np.float32)]

        def read_available(self):
            return self.chunks.pop(0)

    view = LivePitchPlot(SR)
    view.build_figure()
    view._mic = FakeMic()
    view._tick(0)
    view._tick(0)  # nothing new: still fine
    assert view.readout.get_text().startswith("E4")
    assert "3 blocks dropped" in view.status.get_text()


# --- files / CLI -------------------------------------------------------------------


def test_wav_roundtrip_without_extra_packages(tmp_path):
    x = sine(220.0, 0.2)
    path = save_wav(tmp_path / "a.wav", x, SR)
    sig = _load_wave(path, mono=True)
    assert sig.sample_rate == SR and sig.n_samples == len(x)
    np.testing.assert_allclose(sig.samples, x, atol=2 / 32767)
    assert load_audio(path).n_samples == len(x)


def test_load_24bit_stereo_wav(tmp_path):
    vals = np.array([[0, 1], [-1, 4194304], [-8388608, 8388607]], dtype=np.int32)
    raw = b"".join(int(v).to_bytes(3, "little", signed=True) for v in vals.ravel())
    path = tmp_path / "s24.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(3), w.setframerate(8000)
        w.writeframes(raw)
    sig = _load_wave(path, mono=False)
    np.testing.assert_allclose(sig.samples, vals / 8388608.0)


def test_pitch_cli_saves_figure(tmp_path, capsys):
    from dsfp_tune.cli import pitch_main

    wav = save_wav(tmp_path / "take.wav", sine(196.0, 0.5), SR)
    png = tmp_path / "take.png"
    pitch_main([str(wav), "--save", str(png), "--no-show"])
    assert png.exists() and png.stat().st_size > 10_000
    assert "G3" in capsys.readouterr().out
