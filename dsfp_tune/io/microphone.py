"""Microphone input via the ``sounddevice`` package (PortAudio).

Two ways to get audio:

* :func:`record` — blocking: record N seconds and return an :class:`AudioSignal`.
* :class:`MicrophoneStream` — streaming: a context manager that delivers small blocks
  of samples as they arrive, for real-time processing::

      with MicrophoneStream(sample_rate=44100, blocksize=256) as mic:
          for block in mic:            # float32, shape (blocksize,)
              ...

By default the audio callback (on PortAudio's thread) only copies each block into a queue;
all processing happens in the caller's thread, so slow Python code causes dropped blocks
(counted in ``MicrophoneStream.overflows``), never audio-driver glitches.

With ``use_callback=False`` there is no background thread at all: :meth:`MicrophoneStream.read`
and :meth:`MicrophoneStream.read_available` pull audio straight from PortAudio's buffer on the
calling thread. Use this inside GUI event loops — matplotlib's macOS window loop holds Python's
GIL, which starves callback threads so no audio arrives.

macOS note: if the app running Python (Terminal, iTerm, Visual Studio Code, ...) has not been
granted microphone access, recordings come back as pure silence. Allow it under
System Settings → Privacy & Security → Microphone, then restart that app.
"""

from __future__ import annotations

import queue

import numpy as np

from dsfp_tune.io.audio_io import AudioSignal

__all__ = ["MicrophoneStream", "list_input_devices", "record"]


def _sd():
    try:
        import sounddevice
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(
            "Microphone input needs the 'sounddevice' package: pip install sounddevice"
        ) from exc
    return sounddevice


def list_input_devices() -> list[dict]:
    """Input-capable audio devices: ``[{index, name, channels, default_samplerate, is_default}]``."""
    sd = _sd()
    default_in = sd.default.device[0]
    out = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            out.append(
                dict(
                    index=i,
                    name=dev["name"],
                    channels=int(dev["max_input_channels"]),
                    default_samplerate=float(dev["default_samplerate"]),
                    is_default=(i == default_in),
                )
            )
    return out


def record(seconds: float, sample_rate: int = 44100, device: int | str | None = None) -> AudioSignal:
    """Record ``seconds`` of mono audio from the microphone (blocking)."""
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    sd = _sd()
    n = int(round(seconds * sample_rate))
    data = sd.rec(n, samplerate=sample_rate, channels=1, dtype="float32", device=device)
    sd.wait()
    return AudioSignal(np.ascontiguousarray(data[:, 0]), int(sample_rate))


class MicrophoneStream:
    """Stream mono float32 blocks from the microphone.

    Parameters
    ----------
    sample_rate:
        Requested rate in Hz.
    blocksize:
        Samples per block. Smaller = lower latency, more overhead.
    device:
        Device index or a substring of its name; ``None`` = system default.
    max_queued_blocks:
        Callback mode: if the consumer falls this far behind, new blocks are dropped (and counted).
    use_callback:
        ``True`` (default): a PortAudio callback thread fills a queue. ``False``: poll PortAudio
        from the calling thread (see module docs); its buffer holds about ``latency`` seconds.
    latency:
        PortAudio latency setting (seconds or ``"low"``/``"high"``). In polling mode this is
        roughly how long you can go between reads before audio is lost.
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        blocksize: int = 256,
        device: int | str | None = None,
        max_queued_blocks: int = 256,
        use_callback: bool = True,
        latency: float | str | None = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.blocksize = int(blocksize)
        self.device = device
        self.use_callback = bool(use_callback)
        self.latency = latency if latency is not None else ("low" if use_callback else 0.2)
        self.overflows = 0  # blocks lost (driver overflow or full queue)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queued_blocks)
        self._stream = None

    # Called by PortAudio on its own thread: keep it minimal.
    def _callback(self, indata, frames, time, status) -> None:
        if status and status.input_overflow:
            self.overflows += 1
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.overflows += 1

    def start(self) -> "MicrophoneStream":
        sd = _sd()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            device=self.device,
            channels=1,
            dtype="float32",
            latency=self.latency,
            callback=self._callback if self.use_callback else None,
        )
        self._stream.start()
        return self

    def _read_stream(self, frames: int) -> np.ndarray:
        if self._stream is None:
            raise RuntimeError("stream not started (use `with MicrophoneStream(...) as mic:`)")
        data, overflowed = self._stream.read(frames)
        if overflowed:
            self.overflows += 1
        return np.ascontiguousarray(data[:, 0])

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read(self, timeout: float | None = 1.0) -> np.ndarray:
        """Next block of ``blocksize`` samples.

        Callback mode waits up to ``timeout`` seconds (raises ``queue.Empty``); polling mode
        waits until the block has been captured.
        """
        if not self.use_callback:
            return self._read_stream(self.blocksize)
        return self._queue.get(timeout=timeout)

    def read_available(self) -> np.ndarray:
        """Everything captured since the last read, concatenated (possibly empty). Never waits."""
        if not self.use_callback:
            n = self._stream.read_available if self._stream is not None else 0
            return self._read_stream(n) if n > 0 else np.zeros(0, dtype=np.float32)
        blocks = []
        while True:
            try:
                blocks.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def __iter__(self):
        while True:
            yield self.read()

    def __enter__(self) -> "MicrophoneStream":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
