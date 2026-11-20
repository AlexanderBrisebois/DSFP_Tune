"""Cloudflare tunnel wrapper, with a fake `cloudflared` on PATH (no network needed)."""

import os
import stat

import pytest

from dsfp_tune.web.tunnel import CloudflareTunnel, TunnelError


def fake_cloudflared(tmp_path, body):
    exe = tmp_path / "cloudflared"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def test_reports_public_url_and_stops(tmp_path, monkeypatch):
    bindir = fake_cloudflared(tmp_path, (
        'echo "INF Requesting new quick Tunnel on trycloudflare.com..." >&2\n'
        'echo "INF |  https://happy-tuna-sings-loud.trycloudflare.com  |" >&2\n'
        'exec sleep 30\n'))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    with CloudflareTunnel("http://127.0.0.1:8000") as t:
        assert t.public_url == "https://happy-tuna-sings-loud.trycloudflare.com"
        assert t.running
    assert not t.running


def test_missing_binary_says_how_to_install(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(TunnelError, match="brew install cloudflared"):
        CloudflareTunnel("http://127.0.0.1:8000").start()


def test_exits_without_url(tmp_path, monkeypatch):
    bindir = fake_cloudflared(tmp_path, 'echo "ERR failed to request quick Tunnel" >&2\nexit 1\n')
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(TunnelError, match="failed to request quick Tunnel"):
        CloudflareTunnel("http://127.0.0.1:8000").start(timeout=5)
