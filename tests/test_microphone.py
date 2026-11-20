"""Microphone, streaming and WAV tests. A fake `sounddevice` module stands in for real hardware."""

import sys
import types
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from dsfp_tune.analysis import YinStream
from dsfp_tune.theory import nearest_note
from dsfp_tune.io import AudioSignal, save_wav

SR = 44100


def sine(f, seconds, sr=SR, amp=0.5):
    return (amp * np.sin(2 * np.pi * f * np.arange(int(seconds * sr)) / sr)).astype(np.float32)


# --- fake sounddevice --------------------------------------------------------


class FakeInputStream:
    """Callback mode: delivers `signal` to the callback in blocks when started (synchronously).
    Polling mode (callback=None): serves `signal` through read() / read_available."""

    signal = np.zeros(0, np.float32)

    def __init__(self, samplerate, blocksize, device, channels, dtype, latency, callback):
        self.blocksize, self.callback, self.latency = blocksize, callback, latency
        self._pos = 0

    def start(self):
        if self.callback is None:
            return
        x = type(self).signal
        for i in range(0, len(x) - self.blocksize + 1, self.blocksize):
            self.callback(x[i : i + self.blocksize, None], self.blocksize, None, None)

    @property
    def read_available(self):
        return len(type(self).signal) - self._pos

    def read(self, frames):
        data = type(self).signal[self._pos : self._pos + frames, None]
        self._pos += frames
        return data, False

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_sd(monkeypatch):
    sd = types.ModuleType("sounddevice")
    sd.default = SimpleNamespace(device=[0, 1])
    sd.query_devices = lambda: [
        dict(name="Built-in Mic", max_input_channels=1, default_samplerate=48000.0),
        dict(name="Speakers", max_input_channels=0, default_samplerate=48000.0),
        dict(name="USB Interface", max_input_channels=2, default_samplerate=44100.0),
    ]
    sd.recorded = sine(220.0, 1.0)
    sd.rec = lambda n, samplerate, channels, dtype, device: sd.recorded[:n, None].copy()
    sd.wait = lambda: None
    sd.InputStream = FakeInputStream
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    return sd


def test_list_input_devices(fake_sd):
    from dsfp_tune.io.microphone import list_input_devices

    devs = list_input_devices()
    assert [d["index"] for d in devs] == [0, 2]  # speakers excluded
    assert devs[0]["is_default"] and not devs[1]["is_default"]


def test_record_returns_mono_signal(fake_sd):
    from dsfp_tune.io.microphone import record

    sig = record(0.5, sample_rate=SR)
    assert isinstance(sig, AudioSignal)
    assert sig.samples.ndim == 1 and sig.n_samples == SR // 2 and sig.sample_rate == SR
    with pytest.raises(ValueError):
        record(0)


def test_microphone_stream_blocks(fake_sd):
    from dsfp_tune.io.microphone import MicrophoneStream

    FakeInputStream.signal = np.arange(1024, dtype=np.float32)
    with MicrophoneStream(sample_rate=SR, blocksize=256) as mic:
        blocks = [mic.read(timeout=0.1) for _ in range(4)]
    np.testing.assert_array_equal(np.concatenate(blocks), FakeInputStream.signal)
    assert mic.overflows == 0


def test_microphone_stream_polling_mode(fake_sd):
    from dsfp_tune.io.microphone import MicrophoneStream

    FakeInputStream.signal = np.arange(1000, dtype=np.float32)
    with MicrophoneStream(blocksize=256, use_callback=False) as mic:
        assert mic._stream.callback is None and mic._stream.latency == 0.2
        first = mic.read()
        rest = mic.read_available()
        assert mic.read_available().size == 0  # nothing new
    np.testing.assert_array_equal(np.concatenate([first, rest]), FakeInputStream.signal)
    with pytest.raises(RuntimeError):
        mic.read()  # stopped


def test_microphone_stream_counts_dropped_blocks(fake_sd):
    from dsfp_tune.io.microphone import MicrophoneStream

    FakeInputStream.signal = np.zeros(256 * 10, np.float32)
    with MicrophoneStream(blocksize=256, max_queued_blocks=4) as mic:
        pass
    assert mic.overflows == 6


# --- streaming YIN -------------------------------------------------------------


@pytest.mark.parametrize("block", [64, 256, 1000, 4096])
def test_yin_stream_any_block_size_gives_same_estimates(block):
    x = sine(220.0, 0.5)
    ref = YinStream(SR, hop_length=256)
    expected = ref.push(x)

    s = YinStream(SR, hop_length=256)
    got = []
    for i in range(0, len(x), block):
        got += s.push(x[i : i + block])
    assert len(got) == len(expected) == len(x) // 256
    np.testing.assert_array_equal([e.f0 for e in got], [e.f0 for e in expected])


def test_yin_stream_matches_frame_detector():
    x = sine(330.0, 0.5)
    stream = YinStream(SR, hop_length=256)
    ests = stream.push(x)
    n = stream.yin.frame_length
    first_full = -(-n // 256) - 1  # first estimate whose buffer holds no initial zeros
    checked = 0
    # Once the buffer is full, estimate k covers x[(k+1)*256 - n : (k+1)*256].
    for k in range(first_full, len(ests)):
        end = (k + 1) * 256
        direct = stream.yin.detect(x[end - n : end])
        assert ests[k].voiced and ests[k].f0 == pytest.approx(direct.f0)
        checked += 1
    assert checked > 50
    assert abs(1200 * np.log2(ests[-1].f0 / 330.0)) < 5
    stream.reset()
    assert not stream.push(np.zeros(256))[0].voiced


# --- notes -------------------------------------------------------------------


def test_nearest_note():
    name, cents = nearest_note(445.0)
    assert name == "A4" and cents == pytest.approx(19.56, abs=0.01)
    name, cents = nearest_note(452.9)  # closer to A#4
    assert name == "A#4" and cents < 0
    assert nearest_note(452.9, flats=True)[0] == "Bb4"
    with pytest.raises(ValueError):
        nearest_note(0.0)


# --- WAV ---------------------------------------------------------------------


def test_save_wav_roundtrip(tmp_path):
    x = sine(440.0, 0.1)
    x[0], x[1] = 1.5, -1.5  # out of range -> clipped
    path = save_wav(tmp_path / "sub" / "a.wav", AudioSignal(x, SR))
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SR)
        y = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767
    assert len(y) == len(x)
    assert y[0] == 1.0 and y[1] == -1.0
    np.testing.assert_allclose(y[2:], x[2:], atol=1 / 32767)


def test_save_wav_needs_rate(tmp_path):
    with pytest.raises(ValueError):
        save_wav(tmp_path / "a.wav", np.zeros(10))


# --- CLI (tune.py) -------------------------------------------------------------------------


def test_record_cli_saves_and_summarizes(fake_sd, tmp_path, capsys):
    from tune import main

    out = tmp_path / "take.wav"
    main(["record", "--seconds", "0.5", "--out", str(out), "--no-plot"])
    text = capsys.readouterr().out
    assert out.exists()
    assert "A3" in text and "voiced" in text
    assert "showing plot" not in text


def test_record_cli_plots_by_default(fake_sd, tmp_path, capsys, monkeypatch):
    import matplotlib.pyplot as plt

    from tune import main

    shown = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(plt.gcf()))
    main(["record", "--seconds", "0.5", "--out", str(tmp_path / "t.wav")])
    assert len(shown) == 1 and len(shown[0].axes) >= 3
    assert "showing plot" in capsys.readouterr().out


def test_record_cli_warns_on_silence(fake_sd, tmp_path, capsys):
    from tune import main

    fake_sd.recorded = np.zeros(SR, np.float32)
    main(["record", "--seconds", "0.5", "--no-save"])
    assert "Microphone" in capsys.readouterr().err


def test_list_devices_cli(fake_sd, capsys):
    from tune import main

    main(["devices"])
    text = capsys.readouterr().out
    assert "Built-in Mic" in text and "USB Interface" in text and "Speakers" not in text


def test_cents_bar():
    from tune import _cents_bar

    assert _cents_bar(0) == "[----------*----------]"
    assert _cents_bar(-50) == "[*---------|----------]"
    assert _cents_bar(80) == "[----------|---------*]"
