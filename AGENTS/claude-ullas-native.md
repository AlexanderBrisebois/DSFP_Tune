# Agent: Claude (working for Ullas): use the team's C++ DSP from Python

**Status:** done, waiting for Ullas to build it on the Mac and push
**Last updated:** 2026-11-20 16:20 PT

## Done
| What | Files | Commit |
|---|---|---|
| Merged Alex's C++ port / plugin (`82fe03a`, `09e9c3d`) into local main with Ullas's dashboard commits. No conflicts | | merge commit |
| pybind11 bindings for Alex's C++ `Yin` and `FrequencyShifter` (`cpp/src/dsp`, unchanged), imported as `dsfp_tune._native` | `cpp/python/bindings.cpp` | this commit |
| Build script (no CMake/JUCE needed): `pip install pybind11` then `bash scripts/build_native.sh` writes `dsfp_tune/_native*.so` (ignored by git via `*.so`) | `scripts/build_native.sh` | this commit |
| Python switches to C++ automatically when built: `Yin.detect()` and `FrequencyShifter.process()/reset()` call the C++; everything on top (`YinStream`, `yin_track`, pitch hold, `PitchCorrector` incl. harmony, `correct_pitch`, dashboard, CLI) uses it. NumPy fallback when not built, or with `DSFP_TUNE_NATIVE=0`. `dsfp_tune.native.backend()` says which is in use | `dsfp_tune/native.py`, `dsfp_tune/analysis/yin.py`, `dsfp_tune/synthesis/freq_shift.py` | this commit |
| Tests: C++ vs NumPy for YIN (several settings), shifter (odd block sizes, changing ratios, reset), whole `correct_pitch` with harmony. Full suite passes with and without the C++ (Linux VM build) | `tests/test_native.py` | this commit |

## Findings
- Speed (10 s of audio, Linux VM): `correct_pitch` 0.26 s NumPy -> 0.21 s C++; `yin_track` 0.09 -> 0.08 s; `freq_shift` 0.08 -> 0.07 s. Only ~20% faster: NumPy was already vectorised, and the time is in the FFTs themselves (C++ YIN detect ~45 us/frame). A faster FFT in `cpp/src/dsp/Fft.cpp` (e.g. Apple Accelerate vDSP or pocketfft) is where a real speed-up would come from. That's the C++ team's code; I didn't change it.
- The C++ `PitchCorrector` isn't used from Python: it has no pitch hold or chord harmony yet, and Python's control logic per hop is cheap (profile: ~85% of the time is YIN + shifter).

## Notes for other agents
- If you change the C++ `Yin` or `FrequencyShifter` constructor/arguments, update `cpp/python/bindings.cpp` too. Argument checks stay in Python.
- `FrequencyShifter.frames_processed` only counts on the NumPy path (nothing uses it).
