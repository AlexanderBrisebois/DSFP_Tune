import shutil
import subprocess

import numpy as np
import pytest

from dsfp_tune import AudioSignal, load_audio

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_mp3(path, freq=440.0, sr=44100, seconds=1.0, channels=1):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:sample_rate={sr}:duration={seconds}",
         "-ac", str(channels), "-codec:a", "libmp3lame", "-b:a", "192k", str(path)],
        check=True,
    )
    return path


def _peak_freq(x, sr):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / sr)[np.argmax(spec)]


def test_signal_time_axis():
    sig = AudioSignal(np.zeros(100, dtype=np.float32), 50)
    assert sig.duration == 2.0
    assert sig.time[1] == pytest.approx(0.02)
    t, x = sig.time_amplitude()
    assert t.shape == x.shape


@needs_ffmpeg
def test_load_mp3_ffmpeg(tmp_path):
    mp3 = _make_mp3(tmp_path / "a440.mp3")
    sig = load_audio(mp3, backend="ffmpeg")
    assert sig.sample_rate == 44100
    assert sig.samples.dtype == np.float32 and sig.samples.ndim == 1
    assert sig.duration == pytest.approx(1.0, abs=0.06)  # MP3 adds encoder padding
    assert np.abs(sig.samples).max() <= 1.0
    assert _peak_freq(sig.samples, sig.sample_rate) == pytest.approx(440, abs=2)


@needs_ffmpeg
def test_resample_and_stereo(tmp_path):
    mp3 = _make_mp3(tmp_path / "st.mp3", channels=2)
    sig = load_audio(mp3, sample_rate=22050, mono=False, backend="ffmpeg")
    assert sig.sample_rate == 22050
    assert sig.n_channels == 2
    assert _peak_freq(sig.to_mono().samples, 22050) == pytest.approx(440, abs=2)


@needs_ffmpeg
def test_auto_backend(tmp_path):
    sig = load_audio(_make_mp3(tmp_path / "x.mp3", freq=220))
    assert _peak_freq(sig.samples, sig.sample_rate) == pytest.approx(220, abs=2)


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        load_audio("does/not/exist.mp3")
