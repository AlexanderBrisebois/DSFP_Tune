#!/usr/bin/env bash
# Build dsfp_tune/_native: Python bindings for the team's C++ DSP (cpp/src/dsp).
# With it built, Yin and FrequencyShifter (and everything on top of them) run in C++;
# without it, the NumPy code is used. See dsfp_tune/native.py.
#
# Needs a C++17 compiler (macOS: xcode-select --install) and pybind11 (pip install pybind11).
# Usage, from the repo root, with the Python you run tune.py with:
#     bash scripts/build_native.sh            # or: PYTHON=/path/to/python bash scripts/build_native.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"

if ! "$PY" -c "import pybind11" 2>/dev/null; then
    echo "pybind11 is missing: run  $PY -m pip install pybind11" >&2
    exit 1
fi
SUFFIX="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
OUT="dsfp_tune/_native${SUFFIX}"
EXTRA=()
[[ "$(uname)" == Darwin ]] && EXTRA=(-undefined dynamic_lookup)

${CXX:-c++} -O3 -shared -fPIC -std=c++17 "${EXTRA[@]}" \
    $("$PY" -m pybind11 --includes) -Icpp/src \
    cpp/python/bindings.cpp cpp/src/dsp/Fft.cpp cpp/src/dsp/Yin.cpp cpp/src/dsp/FrequencyShifter.cpp \
    -o "$OUT"

"$PY" -c "from dsfp_tune import native; print('built $OUT, backend:', native.backend())"
