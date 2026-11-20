"""Public link for the dashboard through a Cloudflare quick tunnel.

``python tune.py web --public`` runs ``cloudflared tunnel --url http://127.0.0.1:<port>``
next to the local server. Cloudflare prints a random ``https://<words>.trycloudflare.com``
address that forwards to this computer; it changes on every run and stops working when
the tunnel process exits. No Cloudflare account is needed. Install with::

    brew install cloudflared

The server keeps listening on 127.0.0.1 only; cloudflared connects to it locally.
"""

from __future__ import annotations

import atexit
import re
import shutil
import subprocess
import threading
from collections import deque

INSTALL_HINT = (
    "cloudflared is not installed. Install it with:\n"
    "    brew install cloudflared\n"
    "(other systems: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)"
)

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelError(RuntimeError):
    """cloudflared is missing, or the tunnel did not come up."""


class CloudflareTunnel:
    """Run ``cloudflared`` for one local URL and report its public address.

    Use as a context manager, or call :meth:`start` and :meth:`stop`. The process is also
    stopped when Python exits.
    """

    def __init__(self, local_url: str, binary: str = "cloudflared") -> None:
        self.local_url = local_url
        self.binary = binary
        self.public_url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._found = threading.Event()
        self._log: deque[str] = deque(maxlen=30)

    def start(self, timeout: float = 30.0) -> str:
        exe = shutil.which(self.binary)
        if exe is None:
            raise TunnelError(INSTALL_HINT)
        self._proc = subprocess.Popen(
            [exe, "tunnel", "--no-autoupdate", "--url", self.local_url],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        atexit.register(self.stop)
        threading.Thread(target=self._read_output, daemon=True).start()
        if not self._found.wait(timeout) or self.public_url is None:
            log = "\n    ".join(self._log) or "(no output)"
            self.stop()
            raise TunnelError(f"cloudflared did not report a public URL within {timeout:.0f} s. Its output:\n    {log}")
        return self.public_url

    def _read_output(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._log.append(line.rstrip())
            if self.public_url is None:
                m = _URL_RE.search(line)
                if m:
                    self.public_url = m.group(0)
                    self._found.set()
        self._found.set()   # process ended: wake start() either way

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def __enter__(self) -> "CloudflareTunnel":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
