"""Dashboard: choosing the pitch detector (YIN or FFT peak)."""

import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from dsfp_tune.io import save_wav
from dsfp_tune.web import SnapSettings, load_settings, save_settings
from dsfp_tune.web.server import analyze_samples, autotune_samples, make_server, theory_payload

SR = 16000


def tone(freq, seconds=1.0, sr=SR):
    t = np.arange(int(sr * seconds)) / sr
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_setting_defaults_to_yin_and_validates(tmp_path):
    assert SnapSettings().pitch_method == "yin"
    assert SnapSettings.from_dict({"pitch_method": "FFT"}).pitch_method == "fft"
    with pytest.raises(ValueError):
        SnapSettings.from_dict({"pitch_method": "cepstrum"})
    path = tmp_path / "s.json"
    save_settings(SnapSettings(pitch_method="fft"), path)
    assert load_settings(path).pitch_method == "fft"
    assert theory_payload()["pitch_methods"] == ["yin", "fft"]


@pytest.mark.parametrize("method", ["yin", "fft"])
def test_analyze_samples_with_either_method(method):
    x = np.concatenate([np.zeros(SR // 4, np.float32), tone(220.0)])
    out = analyze_samples(x, SR, method)
    assert out["pitch_method"] == method
    f0 = [f for f in out["f0"] if f is not None]
    assert np.median(f0) == pytest.approx(220.0, rel=2e-3)
    assert out["f0"][0] is None


def test_autotune_samples_reports_method():
    x = tone(220 * 2 ** (30 / 1200), 1.0)
    _, summary = autotune_samples(x, SR, SnapSettings("chromatic", pitch_method="fft"))
    assert summary["pitch_method"] == "fft" and summary["voiced_fraction"] > 0.6
    assert summary["mean_shift_cents"] == pytest.approx(30, abs=5)


@pytest.fixture
def server(tmp_path):
    srv = make_server("127.0.0.1", 0, settings_path=tmp_path / "settings.json")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()
    srv.server_close()


def post(url, body, ctype="application/octet-stream", headers=None):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": ctype, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), r.headers


def test_endpoints_follow_query_then_saved_setting(server):
    base, tmp = server
    wav = tmp / "a.wav"
    save_wav(wav, tone(330.0), SR)
    body, _ = post(f"{base}/api/analyze?pitch_method=fft", wav.read_bytes(), headers={"X-Filename": "a.wav"})
    out = json.loads(body)
    assert out["pitch_method"] == "fft"
    assert np.median([f for f in out["f0"] if f]) == pytest.approx(330.0, rel=2e-3)

    # Without the query parameter the saved setting is used.
    post(f"{base}/api/settings", json.dumps({"mode": "chromatic", "pitch_method": "fft"}).encode(), "application/json")
    assert json.loads(post(f"{base}/api/analyze", wav.read_bytes(), headers={"X-Filename": "a.wav"})[0])["pitch_method"] == "fft"
    live = json.loads(post(f"{base}/api/live/start", json.dumps({"sample_rate": SR}).encode(), "application/json")[0])
    assert live["pitch_method"] == "fft"
    ests = json.loads(post(f"{base}/api/live/{live['id']}", tone(330.0).astype("<f4").tobytes())[0])["estimates"]
    voiced = [e[0] for e in ests if e[0]]
    assert voiced and np.median(voiced) == pytest.approx(330.0, rel=2e-3)

    live = json.loads(post(f"{base}/api/live/start", json.dumps({"sample_rate": SR, "pitch_method": "yin"}).encode(),
                           "application/json")[0])
    assert live["pitch_method"] == "yin"

    _, headers = post(f"{base}/api/autotune?sample_rate={SR}", tone(330.0).astype("<f4").tobytes())
    assert json.loads(headers["X-Autotune-Summary"])["pitch_method"] == "fft"
    _, headers = post(f"{base}/api/autotune?sample_rate={SR}&pitch_method=yin", tone(330.0).astype("<f4").tobytes())
    assert json.loads(headers["X-Autotune-Summary"])["pitch_method"] == "yin"

    for url, data in ((f"{base}/api/analyze?pitch_method=hps", wav.read_bytes()),
                      (f"{base}/api/live/start", json.dumps({"sample_rate": SR, "pitch_method": "hps"}).encode())):
        with pytest.raises(urllib.error.HTTPError) as err:
            post(url, data)
        assert err.value.code == 400


def test_page_has_pitch_method_switch(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/", timeout=10) as r:
        html = r.read().decode()
    assert 'id="pitchMethod"' in html and 'data-method="fft"' in html
