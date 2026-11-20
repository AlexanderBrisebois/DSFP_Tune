"""Load audio files (MP3, WAV, FLAC, ...) into NumPy arrays.

The result is an :class:`AudioSignal`: float32 samples in [-1, 1] (amplitude),
the sample rate, and a matching time axis in seconds.

Two decoding backends are supported:

* ``"ffmpeg"``    – calls the ``ffmpeg`` command-line tool and reads raw float32
                    PCM from its stdout. Handles virtually every format and can
                    resample/downmix. Needs only NumPy on the Python side.
* ``"soundfile"`` – uses the ``soundfile`` package (libsndfile >= 1.1 reads MP3).
                    No external binary needed, but cannot resample.

``backend="auto"`` tries soundfile first when no resampling is requested, then (for
``.wav``) Python's built-in ``wave`` module, and finally ffmpeg.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


class AudioLoadError(RuntimeError):
    """Raised when an audio file cannot be decoded by any available backend."""


@dataclass
class AudioSignal:
    """Decoded audio.

    Attributes
    ----------
    samples:
        float32 array. Shape ``(n_samples,)`` for mono or
        ``(n_samples, n_channels)`` for multichannel. Values are in [-1, 1].
    sample_rate:
        Samples per second (Hz).
    source:
        Path the audio was loaded from, if any.
    """

    samples: np.ndarray
    sample_rate: int
    source: Path | None = field(default=None)

    @property
    def n_samples(self) -> int:
        return int(self.samples.shape[0])

    @property
    def n_channels(self) -> int:
        return 1 if self.samples.ndim == 1 else int(self.samples.shape[1])

    @property
    def duration(self) -> float:
        """Length in seconds."""
        return self.n_samples / self.sample_rate

    @property
    def time(self) -> np.ndarray:
        """Time of each sample in seconds, shape ``(n_samples,)``."""
        return np.arange(self.n_samples, dtype=np.float64) / self.sample_rate

    def to_mono(self) -> "AudioSignal":
        """Average channels into one."""
        if self.samples.ndim == 1:
            return self
        return AudioSignal(self.samples.mean(axis=1).astype(np.float32), self.sample_rate, self.source)

    def time_amplitude(self) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(time_seconds, amplitude)`` arrays."""
        return self.time, self.samples

    def __repr__(self) -> str:
        return (
            f"AudioSignal(duration={self.duration:.3f}s, sample_rate={self.sample_rate}, "
            f"channels={self.n_channels}, n_samples={self.n_samples}, source={self.source})"
        )


def load_audio(
    path: str | Path,
    sample_rate: int | None = None,
    mono: bool = True,
    backend: str = "auto",
) -> AudioSignal:
    """Decode an audio file into an :class:`AudioSignal`.

    Parameters
    ----------
    path:
        File to load (``.mp3``, ``.wav``, ``.flac``, ``.m4a``, ...).
    sample_rate:
        Resample to this rate. ``None`` keeps the file's native rate.
    mono:
        Downmix to a single channel (the default; pitch tracking works on mono).
    backend:
        ``"auto"``, ``"ffmpeg"`` or ``"soundfile"``.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    if backend == "auto":
        errors = []
        if sample_rate is None:
            try:
                return _load_soundfile(path, mono)
            except Exception as exc:  # missing package or unsupported format
                errors.append(f"soundfile: {exc}")
            if path.suffix.lower() == ".wav":
                try:
                    return _load_wave(path, mono)
                except Exception as exc:  # e.g. float or compressed WAV
                    errors.append(f"wave: {exc}")
        try:
            return _load_ffmpeg(path, sample_rate, mono)
        except Exception as exc:
            errors.append(f"ffmpeg: {exc}")
        raise AudioLoadError(f"Could not decode {path}:\n  " + "\n  ".join(errors))
    if backend == "ffmpeg":
        return _load_ffmpeg(path, sample_rate, mono)
    if backend == "soundfile":
        if sample_rate is not None:
            raise ValueError("The soundfile backend cannot resample; use backend='ffmpeg'.")
        return _load_soundfile(path, mono)
    raise ValueError(f"Unknown backend {backend!r}")


def _load_soundfile(path: Path, mono: bool) -> AudioSignal:
    import soundfile as sf  # optional dependency

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    sig = AudioSignal(data if data.shape[1] > 1 else data[:, 0], int(sr), path)
    return sig.to_mono() if mono else sig


def _load_wave(path: Path, mono: bool) -> AudioSignal:
    """Read an integer-PCM WAV with the standard library (no extra packages needed)."""
    with wave.open(str(path), "rb") as w:
        width, channels, sr = w.getsampwidth(), w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 1:
        data = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        data = (((v << 8) >> 8).astype(np.float32)) / 8388608.0  # sign-extend 24-bit
    elif width == 4:
        data = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioLoadError(f"unsupported WAV sample width {width} bytes")
    data = data.reshape(-1, channels)
    sig = AudioSignal(data if channels > 1 else data[:, 0].copy(), int(sr), path)
    return sig.to_mono() if mono else sig


def _probe(path: Path) -> tuple[int, int]:
    """Return (sample_rate, channels) of the first audio stream via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise AudioLoadError("ffprobe not found on PATH (it ships with ffmpeg)")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    streams = json.loads(out).get("streams", [])
    if not streams:
        raise AudioLoadError(f"No audio stream in {path}")
    return int(streams[0]["sample_rate"]), int(streams[0]["channels"])


def _load_ffmpeg(path: Path, sample_rate: int | None, mono: bool) -> AudioSignal:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioLoadError("ffmpeg not found on PATH (macOS: `brew install ffmpeg`)")

    native_sr, native_ch = _probe(path)
    sr = sample_rate or native_sr
    channels = 1 if mono else native_ch

    cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(path),
           "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", str(channels), "-ar", str(sr), "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise AudioLoadError(proc.stderr.decode(errors="replace").strip())

    data = np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)
    if channels > 1:
        data = data[: len(data) - len(data) % channels].reshape(-1, channels)
    return AudioSignal(data, sr, path)


def wav_bytes(samples: np.ndarray | AudioSignal, sample_rate: int | None = None) -> bytes:
    """Encode audio as a 16-bit PCM WAV file in memory (see :func:`save_wav`)."""
    import io

    if isinstance(samples, AudioSignal):
        sample_rate = samples.sample_rate if sample_rate is None else sample_rate
        samples = samples.samples
    if sample_rate is None:
        raise ValueError("sample_rate is required")
    data = np.asarray(samples, dtype=np.float64)
    channels = 1 if data.ndim == 1 else data.shape[1]
    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def save_wav(path: str | Path, samples: np.ndarray | AudioSignal, sample_rate: int | None = None) -> Path:
    """Write audio to a 16-bit PCM WAV file (standard library only; readable everywhere).

    ``samples`` is float in [-1, 1] (values outside are clipped), shape ``(n,)`` or
    ``(n, channels)``, or an :class:`AudioSignal` (then ``sample_rate`` is taken from it).
    Parent directories are created. Returns the path written.
    """
    data = wav_bytes(samples, sample_rate)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
