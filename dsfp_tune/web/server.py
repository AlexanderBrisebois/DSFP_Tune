"""Local HTTP server for the dashboard (standard library only).

Routes
------
GET  /                      the dashboard page (files in ``static/``)
GET  /api/theory            tonics, scales and every key's notes, from :mod:`dsfp_tune.theory`
GET  /api/settings          saved snap settings
POST /api/settings          save snap settings (JSON body)
POST /api/analyze           audio file as the raw body -> pitch track (load_audio + yin_track)
POST /api/live/start        {"sample_rate": 48000} -> {"id": ...}; one YinStream per mic session
POST /api/live/<id>         raw little-endian float32 mono samples -> pitch estimates
POST /api/live/<id>/stop    forget the session
POST /api/autotune?sample_rate=..&retune_ms=..&mode=..&tonic=..&scale=..&harmony=..
                            [&release_ms=..&amount=..&hysteresis=..&note_window_ms=..
                             &note_jump=..&hold_ms=..&max_correction=..]   (advanced; see ADVANCED_LIMITS)
                            raw little-endian float32 mono samples (a microphone recording)
                            -> pitch-corrected WAV (dsfp_tune.correction.correct_pitch);
                            summary JSON in the X-Autotune-Summary header

The browser decodes the file itself for the waveform and playback; the server does the
pitch analysis and correction, using the same code as the CLI.
"""

from __future__ import annotations

import json
import math
import mimetypes
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np

from dsfp_tune.analysis import YinStream, yin_track
from dsfp_tune.correction import correct_pitch
from dsfp_tune.io import AudioLoadError, load_audio, wav_bytes
from dsfp_tune.theory import NOTE_NAMES_FLAT, NOTE_NAMES_SHARP, SCALES, Key
from dsfp_tune.web.settings import SNAP_MODES, SnapSettings, load_settings, save_settings

STATIC_DIR = Path(__file__).with_name("static")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
LIVE_IDLE_SECONDS = 120.0
MAX_AUTOTUNE_SECONDS = 600.0
DEFAULT_RETUNE_MS = 25.0
# Advanced correction settings accepted by /api/autotune: PitchCorrector argument -> allowed range.
ADVANCED_LIMITS = {
    "release_ms": (0.0, 2000.0),
    "amount": (0.0, 1.0),
    "hysteresis": (0.0, 1.0),
    "note_window_ms": (0.0, 1000.0),
    "note_jump": (0.1, 12.0),
    "hold_ms": (0.0, 1000.0),
    "max_correction": (0.1, 12.0),
}

# Pitch range: the CLI defaults (70-1000 Hz cover singing voices).
F_MIN = 70.0
F_MAX = 1000.0


def _hop_for(sample_rate: int, seconds: float = 0.01) -> int:
    """About 10 ms between pitch frames, whatever the sample rate."""
    return max(64, int(round(sample_rate * seconds)))


def theory_payload() -> dict:
    """Everything the page needs to draw keys: 12 tonics x 15 scales with spelled note names."""
    keys = {}
    for scale in SCALES:
        for tonic in range(12):
            k = Key(tonic, scale)
            keys[f"{tonic}:{scale}"] = {
                "name": k.name,
                "pitch_classes": list(k.pitch_classes),
                "note_names": list(k.note_names),
                "uses_flats": k.uses_flats,
            }
    return {
        "note_names_sharp": list(NOTE_NAMES_SHARP),
        "note_names_flat": list(NOTE_NAMES_FLAT),
        "scales": [{"id": s, "label": s.replace("_", " ").capitalize(), "size": len(SCALES[s])} for s in SCALES],
        "keys": keys,
        "snap_modes": list(SNAP_MODES),
        "a4_hz": 440.0,
    }


def _f0_list(f0: np.ndarray, voiced: np.ndarray) -> list:
    return [round(float(f), 3) if v and math.isfinite(f) else None for f, v in zip(f0, voiced)]


def analyze_samples(samples: np.ndarray, sample_rate: int) -> dict:
    """Pitch track of a mono signal, as JSON-ready lists."""
    hop = _hop_for(sample_rate)
    f_max = min(F_MAX, sample_rate / 4)
    track = yin_track(samples, sample_rate, hop_length=hop, f_min=F_MIN, f_max=f_max)
    conf = np.clip(1.0 - track.aperiodicity, 0.0, 1.0)
    return {
        "sample_rate": int(sample_rate),
        "duration": len(samples) / sample_rate,
        "hop_seconds": hop / sample_rate,
        "t0": float(track.times[0]) if len(track.times) else 0.0,
        "f0": _f0_list(track.f0, track.voiced),
        "confidence": [round(float(c), 3) for c in conf],
        "voiced_fraction": float(track.voiced.mean()) if len(track.voiced) else 0.0,
    }


def analyze_file(path: str | Path) -> dict:
    sig = load_audio(path, mono=True)
    out = analyze_samples(sig.samples, sig.sample_rate)
    out["source"] = Path(path).name
    return out


def autotune_samples(samples: np.ndarray, sample_rate: int, settings: SnapSettings,
                     retune_ms: float = DEFAULT_RETUNE_MS, **advanced: float) -> tuple[bytes, dict]:
    """Pitch-correct a mono recording with the dashboard's snap settings.

    Returns ``(wav_bytes, summary)``. Mode ``chromatic`` snaps to the nearest semitone,
    ``key`` to the nearest note of ``settings.key``; ``off`` is rejected (nothing to do).
    ``settings.harmony`` > 0 adds the key's triad on each snapped note; it applies only in
    ``key`` mode with a 7-note scale and is ignored otherwise. ``advanced`` takes the
    :class:`~dsfp_tune.correction.PitchCorrector` settings listed in ``ADVANCED_LIMITS``
    (release_ms, amount, hysteresis, note_window_ms, note_jump, hold_ms, max_correction).
    """
    sample_rate = int(sample_rate)
    if settings.mode == "off":
        raise ValueError("snapping is off: choose 'Nearest note' or 'Nearest note in key' to autotune")
    if not 8000 <= sample_rate <= 192000:
        raise ValueError(f"unsupported sample rate {sample_rate}")
    if not 0.0 <= retune_ms <= 1000.0:
        raise ValueError("retune_ms must be between 0 and 1000")
    for name, value in advanced.items():
        if name not in ADVANCED_LIMITS:
            raise ValueError(f"unknown setting {name!r}")
        lo, hi = ADVANCED_LIMITS[name]
        if not lo <= value <= hi:
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    x = np.nan_to_num(np.asarray(samples, dtype=np.float64))
    if len(x) == 0:
        raise ValueError("empty recording")
    if len(x) > MAX_AUTOTUNE_SECONDS * sample_rate:
        raise ValueError(f"recording longer than {MAX_AUTOTUNE_SECONDS / 60:.0f} minutes")

    key = settings.key
    harmony = settings.harmony if key is not None and len(key.intervals) == 7 else 0.0
    result = correct_pitch(x, sample_rate, key=key, retune_ms=retune_ms, harmony=harmony,
                           f_min=F_MIN, f_max=min(F_MAX, sample_rate / 4), **advanced)
    samples = result.samples
    peak = float(np.max(np.abs(samples), initial=0.0))
    if harmony and peak > 0.99:  # lead + chord voices can exceed full scale: scale down instead of clipping
        samples = samples * (0.99 / peak)
    shift = np.abs(result.correction[result.voiced]) * 100.0
    summary = {
        "mode": settings.mode,
        "key": key.name if key is not None else None,
        "retune_ms": float(retune_ms),
        "harmony": float(harmony),
        "advanced": {k: float(v) for k, v in advanced.items()},
        "duration": len(x) / sample_rate,
        "voiced_fraction": float(result.voiced.mean()) if len(result.voiced) else 0.0,
        "mean_shift_cents": round(float(shift.mean()), 1) if shift.size else 0.0,
        "max_shift_cents": round(float(shift.max()), 1) if shift.size else 0.0,
    }
    return wav_bytes(samples, sample_rate), summary


class LiveSessions:
    """One streaming YIN per browser microphone session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}

    def start(self, sample_rate: int) -> dict:
        sample_rate = int(sample_rate)
        if not 8000 <= sample_rate <= 192000:
            raise ValueError(f"unsupported sample rate {sample_rate}")
        hop = _hop_for(sample_rate)
        stream = YinStream(sample_rate, hop_length=hop, f_min=F_MIN, f_max=min(F_MAX, sample_rate / 4))
        sid = uuid.uuid4().hex
        with self._lock:
            self._prune()
            self._sessions[sid] = {"stream": stream, "lock": threading.Lock(), "seen": time.monotonic()}
        return {"id": sid, "sample_rate": sample_rate, "hop_seconds": hop / sample_rate,
                "frame_seconds": stream.yin.latency_seconds}

    def push(self, sid: str, block: np.ndarray) -> list:
        with self._lock:
            sess = self._sessions.get(sid)
            if sess is None:
                raise KeyError(sid)
            sess["seen"] = time.monotonic()
        with sess["lock"]:
            ests = sess["stream"].push(block)
        return [[round(e.f0, 3) if e.voiced else None, round(e.confidence, 3)] for e in ests]

    def stop(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def _prune(self) -> None:
        now = time.monotonic()
        for sid in [s for s, v in self._sessions.items() if now - v["seen"] > LIVE_IDLE_SECONDS]:
            del self._sessions[sid]

    def __len__(self) -> int:
        return len(self._sessions)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "DSFPTune/0.1"
    settings_path: Path | None = None          # set by make_server
    live: LiveSessions                          # set by make_server
    quiet = True

    # -- helpers --------------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: D401 - keep the terminal clean
        if not self.quiet:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, separators=(",", ":")).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD_BYTES:
            raise ValueError(f"upload too large ({n / 1e6:.0f} MB, limit {MAX_UPLOAD_BYTES / 1e6:.0f} MB)")
        return self.rfile.read(n) if n else b""

    # -- routes ---------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/theory":
            return self._json(theory_payload())
        if path == "/api/settings":
            return self._json(load_settings(self.settings_path).to_dict())
        if path.startswith("/api/"):
            return self._error(404, "not found")
        return self._static(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/settings":
                s = SnapSettings.from_dict(json.loads(self._body() or b"{}"))
                save_settings(s, self.settings_path)
                return self._json(s.to_dict())
            if path == "/api/analyze":
                return self._analyze()
            if path == "/api/autotune":
                return self._autotune()
            if path == "/api/live/start":
                data = json.loads(self._body() or b"{}")
                return self._json(self.live.start(data.get("sample_rate", 48000)))
            if path.startswith("/api/live/"):
                rest = path[len("/api/live/"):].strip("/").split("/")
                if len(rest) == 2 and rest[1] == "stop":
                    self.live.stop(rest[0])
                    return self._json({"ok": True})
                if len(rest) == 1:
                    raw = self._body()
                    block = np.frombuffer(raw[: len(raw) // 4 * 4], dtype="<f4")
                    return self._json({"estimates": self.live.push(rest[0], block)})
            return self._error(404, "not found")
        except KeyError:
            return self._error(410, "live session expired; press Start again")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._error(400, str(exc))
        except AudioLoadError as exc:
            return self._error(422, str(exc).splitlines()[0] + " (is ffmpeg installed?)")

    def _analyze(self):
        name = Path(self.headers.get("X-Filename") or "upload.mp3").name
        suffix = Path(name).suffix.lower() or ".mp3"
        body = self._body()
        if not body:
            raise ValueError("empty upload")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / f"upload{suffix}"
            p.write_bytes(body)
            started = time.monotonic()
            result = analyze_file(p)
        result["source"] = name
        result["analysis_seconds"] = round(time.monotonic() - started, 3)
        return self._json(result)

    def _autotune(self):
        q = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
        if "sample_rate" not in q:
            raise ValueError("sample_rate query parameter is required")
        saved = load_settings(self.settings_path).to_dict()
        settings = SnapSettings.from_dict({k: q.get(k, saved[k]) for k in ("mode", "tonic", "scale", "harmony")})
        raw = self._body()
        samples = np.frombuffer(raw[: len(raw) // 4 * 4], dtype="<f4")
        started = time.monotonic()
        advanced = {k: float(q[k]) for k in ADVANCED_LIMITS if k in q}
        wav, summary = autotune_samples(samples, int(float(q["sample_rate"])), settings,
                                        float(q.get("retune_ms", DEFAULT_RETUNE_MS)), **advanced)
        summary["processing_seconds"] = round(time.monotonic() - started, 3)
        return self._send(200, wav, "audio/wav", {"X-Autotune-Summary": json.dumps(summary, separators=(",", ":"))})

    def _static(self, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def make_server(host: str = "127.0.0.1", port: int = 8000, settings_path: str | Path | None = None,
                quiet: bool = True) -> ThreadingHTTPServer:
    handler = type("Handler", (DashboardHandler,), {
        "settings_path": Path(settings_path) if settings_path else None,
        "live": LiveSessions(),
        "quiet": quiet,
    })
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True, quiet: bool = True,
          public: bool = False) -> None:
    """Run the dashboard until Ctrl+C. ``public=True`` also opens a Cloudflare quick tunnel
    (see :mod:`dsfp_tune.web.tunnel`) and prints its https://...trycloudflare.com link."""
    server = make_server(host, port, quiet=quiet)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{server.server_address[1]}/"
    tunnel = None
    if public:
        from dsfp_tune.web.tunnel import CloudflareTunnel, TunnelError

        print("Starting Cloudflare tunnel ...")
        tunnel = CloudflareTunnel(f"http://127.0.0.1:{server.server_address[1]}")
        try:
            tunnel.start()
        except TunnelError as exc:
            server.server_close()
            raise SystemExit(f"error: {exc}") from None
    print(f"DSFP Tune dashboard at {url}  (Ctrl+C to stop)")
    if tunnel:
        print(f"Public link: {tunnel.public_url}/")
        print("  Anyone with this link can use the dashboard on this computer. It stops working when you press Ctrl+C.")
    if open_browser:
        import webbrowser

        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        if tunnel:
            tunnel.stop()
        server.server_close()


__all__ = ["ADVANCED_LIMITS", "analyze_file", "analyze_samples", "autotune_samples", "make_server", "serve", "theory_payload"]
