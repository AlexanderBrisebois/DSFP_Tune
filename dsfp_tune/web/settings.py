"""Snap settings chosen in the dashboard.

The dashboard stores what the user picked here so the
correction stage can read it later::

    from dsfp_tune.web import load_settings
    s = load_settings()          # SnapSettings(mode='key', tonic=9, scale='minor')
    s.key                        # Key('A', 'minor'), or None when mode is not 'key'

Modes:
    off        no snapping
    chromatic  snap to the nearest note of the 12-note scale
    key        snap to the nearest note in ``Key(tonic, scale)``

``harmony`` (0..1, default 0 = off) is the level of the added triad voices; it only applies in
``key`` mode with a 7-note scale (pass it as ``PitchCorrector(..., harmony=s.harmony)``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from dsfp_tune.theory import Key, parse_pitch_class

SNAP_MODES = ("off", "chromatic", "key")

#: Default location, inside the git-ignored data/output/ folder.
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "output" / "dashboard_settings.json"

@dataclass(frozen=True)
class SnapSettings:
    mode: str = "chromatic"
    tonic: int = 0            # pitch class 0..11 (C = 0)
    scale: str = "major"
    harmony: float = 0.0      # chord voice level, see dsfp_tune.correction.PitchCorrector

    @classmethod
    def from_dict(cls, data: dict) -> "SnapSettings":
        """Validate and normalise a dict (e.g. JSON from the page). Raises ValueError."""
        if not isinstance(data, dict):
            raise ValueError("settings must be a JSON object")
        mode = str(data.get("mode", cls.mode))
        if mode not in SNAP_MODES:
            raise ValueError(f"mode must be one of {', '.join(SNAP_MODES)}, got {mode!r}")
        tonic = data.get("tonic", cls.tonic)
        if isinstance(tonic, str) and not tonic.lstrip("-").isdigit():
            tonic = parse_pitch_class(tonic)
        tonic = int(tonic)
        if not 0 <= tonic <= 11:
            raise ValueError(f"tonic must be a pitch class 0..11, got {tonic}")
        scale = Key(tonic, str(data.get("scale", cls.scale))).scale   # validates + resolves aliases
        harmony = float(data.get("harmony", cls.harmony))
        if not 0.0 <= harmony <= 1.0:
            raise ValueError(f"harmony must be between 0 and 1, got {harmony}")
        return cls(mode, tonic, scale, harmony)

    @property
    def key(self) -> Key | None:
        return Key(self.tonic, self.scale) if self.mode == "key" else None

    def to_dict(self) -> dict:
        return asdict(self)

def load_settings(path: str | Path | None = None) -> SnapSettings:
    """Read saved settings; defaults if the file is missing or invalid."""
    path = Path(path) if path else DEFAULT_SETTINGS_PATH
    try:
        return SnapSettings.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return SnapSettings()

def save_settings(settings: SnapSettings, path: str | Path | None = None) -> Path:
    path = Path(path) if path else DEFAULT_SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2) + "\n")
    return path
