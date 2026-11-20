"""Dashboard: autotuned playback of a microphone recording (POST /api/autotune)."""

import io
import json
import threading
import urllib.error
import urllib.request
import wave

import numpy as np
import pytest

from dsfp_tune.analysis import yin_track
from dsfp_tune.theory import freq_to_midi
from dsfp_tune.web import SnapSettings, save_settings
from dsfp_tune.web.server import autotune_samples, make_server

SR = 48000  # what browsers usually record at


def sung(midi, cents, seconds=1.2, sr=SR):
    """Harmonic 'voice' held `cents` off `midi`."""
    t = np.arange(int(sr * seconds)) / sr
    f = 440 * 2 ** ((midi - 69 + cents / 100) / 12)
    x = sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate((0.5, 1, 0.7, 0.4, 0.2)))
    return (0.12 * x).astype(np.float32)


def decode_wav(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767.0
    return x, sr


def median_midi(x, sr):
    tr = yin_track(x, sr)
    m = freq_to_midi(tr.f0[tr.voiced])
    return float(np.median(m[len(m) // 5 : -len(m) // 5]))


# -- autotune_samples ------------------------------------------------------------


def test_autotune_samples_chromatic_and_key():
    x = sung(61, -20)  # C#4 -20c
    wav, summary = autotune_samples(x, SR, SnapSettings("chromatic"))
    y, sr = decode_wav(wav)
    assert sr == SR and len(y) == len(x)
    assert median_midi(y, sr) == pytest.approx(61, abs=0.03)
    assert summary["key"] is None and summary["mean_shift_cents"] == pytest.approx(20, abs=3)

    wav, summary = autotune_samples(x, SR, SnapSettings("key", 0, "major"), retune_ms=0)
    assert median_midi(*decode_wav(wav)) == pytest.approx(60, abs=0.03)  # C# not in C major -> C
    assert summary["key"] == "C major" and summary["retune_ms"] == 0


@pytest.mark.parametrize(
    "settings, kwargs, message",
    [
        (SnapSettings("off"), {}, "snapping is off"),
        (SnapSettings(), {"retune_ms": -5}, "retune_ms"),
        (SnapSettings(), {"sample_rate": 4000}, "sample rate"),
    ],
)
def test_autotune_samples_rejects(settings, kwargs, message):
    sr = kwargs.pop("sample_rate", SR)
    with pytest.raises(ValueError, match=message):
        autotune_samples(sung(57, 10, 0.3), sr, settings, **kwargs)
    with pytest.raises(ValueError, match="empty"):
        autotune_samples(np.zeros(0, np.float32), SR, SnapSettings())


# -- HTTP -------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path):
    srv = make_server("127.0.0.1", 0, settings_path=tmp_path / "settings.json")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path / "settings.json"
    srv.shutdown()
    srv.server_close()


def post(url, body):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), r.headers


def test_autotune_endpoint_returns_corrected_wav(server):
    base, _ = server
    x = sung(57, 35)  # A3 +35c
    body, headers = post(f"{base}/api/autotune?sample_rate={SR}&retune_ms=25&mode=chromatic", x.astype("<f4").tobytes())
    assert headers.get_content_type() == "audio/wav"
    summary = json.loads(headers["X-Autotune-Summary"])
    assert summary["mode"] == "chromatic" and summary["voiced_fraction"] > 0.8
    y, sr = decode_wav(body)
    assert sr == SR and len(y) == len(x)
    assert median_midi(y, sr) == pytest.approx(57, abs=0.03)


def test_autotune_endpoint_uses_saved_settings_by_default(server):
    base, settings_path = server
    save_settings(SnapSettings("key", 0, "major"), settings_path)
    body, headers = post(f"{base}/api/autotune?sample_rate={SR}&retune_ms=0", sung(61, -20).astype("<f4").tobytes())
    assert json.loads(headers["X-Autotune-Summary"])["key"] == "C major"
    assert median_midi(*decode_wav(body)) == pytest.approx(60, abs=0.03)


@pytest.mark.parametrize(
    "query, code",
    [("", 400), (f"?sample_rate={SR}&mode=off", 400), (f"?sample_rate={SR}&mode=robot", 400), (f"?sample_rate={SR}&scale=klingon", 400)],
)
def test_autotune_endpoint_errors(server, query, code):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(f"{base}/api/autotune{query}", sung(57, 10, 0.3).astype("<f4").tobytes())
    assert e.value.code == code
    assert "error" in json.loads(e.value.read())


def test_page_has_recording_controls(server):
    base, _ = server
    html = urllib.request.urlopen(base + "/").read().decode()
    for el in ('id="recording"', 'id="playOriginal"', 'id="playTuned"', 'id="retuneRange"', 'id="downloadTuned"'):
        assert el in html
    js = urllib.request.urlopen(base + "/app.js").read().decode()
    assert "/api/autotune" in js and "function finishRecording" in js


def test_autotune_endpoint_advanced_settings(server):
    base, _ = server
    x = sung(57, 40).astype("<f4").tobytes()
    body, headers = post(f"{base}/api/autotune?sample_rate={SR}&mode=chromatic&amount=0.5&release_ms=100"
                         "&hysteresis=0.2&note_window_ms=80&note_jump=1&hold_ms=40&max_correction=3", x)
    summary = json.loads(headers["X-Autotune-Summary"])
    assert summary["advanced"] == {"release_ms": 100, "amount": 0.5, "hysteresis": 0.2, "note_window_ms": 80,
                                   "note_jump": 1, "hold_ms": 40, "max_correction": 3}
    assert 100 * (median_midi(*decode_wav(body)) - 57) == pytest.approx(20, abs=2)  # halfway


@pytest.mark.parametrize("param", ["amount=1.5", "release_ms=-1", "note_jump=0", "max_correction=50", "amount=abc"])
def test_autotune_endpoint_rejects_bad_advanced(server, param):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(f"{base}/api/autotune?sample_rate={SR}&mode=chromatic&{param}", sung(57, 10, 0.3).astype("<f4").tobytes())
    assert e.value.code == 400


def test_autotune_samples_rejects_unknown_setting():
    with pytest.raises(ValueError, match="unknown setting"):
        autotune_samples(sung(57, 10, 0.3), SR, SnapSettings(), threshold=0.2)


def test_page_has_advanced_tab(server):
    base, _ = server
    html = urllib.request.urlopen(base + "/").read().decode()
    assert 'id="settingsTabs"' in html and 'data-pane="advanced"' in html and 'id="advancedReset"' in html
    js = urllib.request.urlopen(base + "/app.js").read().decode()
    for knob in ("retune_ms", "release_ms", "amount", "hysteresis", "note_window_ms", "note_jump", "hold_ms", "max_correction"):
        assert f"id: '{knob}'" in js
