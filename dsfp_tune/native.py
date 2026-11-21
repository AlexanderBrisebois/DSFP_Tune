"""Optional C++ kernels (cpp/src/dsp, the same code as the AU/VST3 plugin).

When the extension ``dsfp_tune._native`` has been built (``bash scripts/build_native.sh``),
:class:`dsfp_tune.analysis.Yin` and :class:`dsfp_tune.synthesis.FrequencyShifter` run their
inner loops in C++. Everything built on them (``YinStream``, ``yin_track``, the pitch hold,
``PitchCorrector``, ``correct_pitch``, the dashboard and the CLI) picks that up automatically.
Results match the NumPy code to floating-point rounding (``tests/test_native.py``).

If the extension isn't built, or ``DSFP_TUNE_NATIVE=0`` is set in the environment, the NumPy
implementations are used instead.
"""

from __future__ import annotations

import os

__all__ = ["AVAILABLE", "backend", "lib"]

lib = None
if os.environ.get("DSFP_TUNE_NATIVE", "1").strip().lower() not in ("0", "false", "off", "no"):
    try:
        from dsfp_tune import _native as lib  # type: ignore[attr-defined, no-redef]
    except ImportError:
        lib = None

#: True when the C++ kernels are in use.
AVAILABLE = lib is not None


def backend() -> str:
    """``"c++"`` or ``"numpy"``: which implementation Yin and FrequencyShifter use."""
    return "c++" if AVAILABLE else "numpy"
