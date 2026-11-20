"""Web dashboard: `python tune.py web`.

A small local server (Python standard library only) that serves the dashboard page and
runs the existing analysis code for it: `load_audio` + `yin_track` for files, `YinStream`
for the browser microphone. Snap settings chosen in the page are saved with
:mod:`dsfp_tune.web.settings` so the pitch-correction stage can read them later.
"""

from dsfp_tune.web.settings import SNAP_MODES, SnapSettings, load_settings, save_settings

__all__ = ["SNAP_MODES", "SnapSettings", "load_settings", "save_settings"]
