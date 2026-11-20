# Agent: Claude (working for Ullas): harness, audio I/O, notes database

**Status:** idle, waiting for the next task from Ullas
**Last updated:** 2026-11-20 14:30 PT

## Done
| What | Files | Commit |
|---|---|---|
| Project structure (CLI tool, not a pip package) | `tune.py`, `dsfp_tune/`, `tests/`, `scripts/`, `notebooks/`, `cpp/`, `data/`, `pytest.ini`, `requirements.txt`, README | `8b4e72a`, `f049e54` |
| MP3/audio import into NumPy: `load_audio()` returns an `AudioSignal` (float32 amplitude in [-1, 1], sample rate, `.time`). Uses ffmpeg, or the `soundfile` package as a fallback | `dsfp_tune/io/audio_io.py`, `tests/test_audio_io.py` | `8b4e72a` |
| CLI: `python tune.py inspect <file> [--plot] [--sr N] [--save out.npz]` | `tune.py` | `f049e54` |
| Notes and keys database: 12-TET, A4 = 440 Hz (configurable). 128 MIDI notes; 12 tonics x 15 scales (major, minor, harmonic/melodic minor, modes, pentatonics, blues, chromatic) | `dsfp_tune/theory/`, `scripts/build_note_db.py`, `data/theory/notes.csv`, `data/theory/keys.json`, `tests/test_theory.py` | `64388b3` |

## In progress
Nothing.

## Not doing (Ullas said to hold off)
- Snapping pitch to the nearest note in a key. That will build on `dsfp_tune/theory` once Ullas asks for it.

## Likely next (not started, unclaimed)
- STFT and pitch (f0) detection in `dsfp_tune/analysis/`
- Pitch shifting (phase vocoder) in `dsfp_tune/synthesis/`

## Conventions other agents should know
- Add new CLI features as subcommands of `tune.py`.
- `dsfp_tune/theory` is the source of truth. Regenerate the `data/theory/` files with `python scripts/build_note_db.py` rather than editing them by hand.
- Run tests with `pytest` from the repo root.
