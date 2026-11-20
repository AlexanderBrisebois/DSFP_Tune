"""Dashboard server: settings, analysis, and the HTTP routes (no browser needed)."""

import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from dsfp_tune.io import save_wav
from dsfp_tune.web import SnapSettings, load_settings, save_settings
from dsfp_tune.web.server import analyze_samples, make_server, theory_payload

SR = 16000


def tone(freq, seconds=1.0, sr=SR):
    t = np.arange(int(sr * seconds)) / sr
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# -- settings ---------------------------------------------------------------

def test_settings_validate_and_normalise():
    s = SnapSettings.from_dict({"mode": "key", "tonic": "Bb", "scale": "min"})
    assert s == SnapSettings("key", 10, "minor")
    assert s.key.pitch_classes == (10, 0, 1, 3, 5, 6, 8)
    assert SnapSettings.from_dict({"mode": "chromatic"}).key is None
    for bad in ({"mode": "nearest"}, {"tonic": 12}, {"scale": "klingon"}, []):
        with pytest.raises(ValueError):
            SnapSettings.from_dict(bad)


def test_settings_round_trip(tmp_path):
    path = tmp_path / "s.json"
    assert load_settings(path) == SnapSettings()          # missing file -> defaults
    save_settings(SnapSettings("key", 9, "dorian"), path)
    assert load_settings(path) == SnapSettings("key", 9, "dorian")
    path.write_text("{not json")
    assert load_settings(path) == SnapSettings()


# -- analysis ---------------------------------------------------------------

def test_theory_payload_has_every_key():
    t = theory_payload()
    assert len(t["keys"]) == 12 * 15
    assert t["keys"]["3:minor"]["note_names"] == ["Eb", "F", "Gb", "Ab", "Bb", "Cb", "Db"]
    assert t["snap_modes"] == ["off", "chromatic", "key"]


def test_analyze_samples_finds_the_pitch():
    x = np.concatenate([np.zeros(SR // 4, np.float32), tone(220.0)])
    out = analyze_samples(x, SR)
    assert out["hop_seconds"] == pytest.approx(0.01)
    f0 = [f for f in out["f0"] if f is not None]
    assert np.median(f0) == pytest.approx(220.0, rel=1e-3)
    assert out["f0"][0] is None                            # leading silence is unvoiced
    assert len(out["f0"]) == len(out["confidence"])


# -- HTTP -------------------------------------------------------------------

@pytest.fixture
def server(tmp_path):
    srv = make_server("127.0.0.1", 0, settings_path=tmp_path / "settings.json")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read()
        return json.loads(body) if r.headers.get_content_type() == "application/json" else body


def test_static_page_and_theory(server):
    assert b"DSFP Tune" in call(server + "/")
    assert b"registerProcessor" in call(server + "/mic-worklet.js")
    assert len(call(server + "/api/theory")["keys"]) == 180
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server + "/../tune.py")
    assert e.value.code == 404


def test_settings_endpoint(server, tmp_path):
    assert call(server + "/api/settings") == {"mode": "chromatic", "tonic": 0, "scale": "major"}
    saved = call(server + "/api/settings", json.dumps({"mode": "key", "tonic": 9, "scale": "minor"}).encode())
    assert saved == {"mode": "key", "tonic": 9, "scale": "minor"}
    assert load_settings(tmp_path / "settings.json") == SnapSettings("key", 9, "minor")
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server + "/api/settings", b'{"mode": "robot"}')
    assert e.value.code == 400


def test_analyze_endpoint(server, tmp_path):
    wav = save_wav(tmp_path / "a.wav", tone(330.0), SR)
    out = call(server + "/api/analyze", wav.read_bytes(), {"X-Filename": "take 1.wav"})
    assert out["source"] == "take 1.wav"
    assert out["sample_rate"] == SR
    assert np.median([f for f in out["f0"] if f]) == pytest.approx(330.0, rel=2e-3)


def test_live_stream(server):
    sess = call(server + "/api/live/start", json.dumps({"sample_rate": SR}).encode())
    x = tone(440.0, 0.5)
    ests = []
    for i in range(0, len(x), 1024):
        ests += call(f"{server}/api/live/{sess['id']}", x[i:i + 1024].astype("<f4").tobytes())["estimates"]
    assert len(ests) == len(x) // round(SR * 0.01)
    assert ests[-1][0] == pytest.approx(440.0, rel=2e-3)
    call(f"{server}/api/live/{sess['id']}/stop", b"")
    with pytest.raises(urllib.error.HTTPError) as e:
        call(f"{server}/api/live/{sess['id']}", x[:1024].tobytes())
    assert e.value.code == 410
