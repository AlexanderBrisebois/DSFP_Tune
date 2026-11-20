# Agent: Claude (working for Ullas): web dashboard

**Status:** waiting for Ullas's feedback on the dashboard
**Last updated:** 2026-11-20 15:10 PT

## Done
| What | Files | Commit |
|---|---|---|
| Web dashboard, `python tune.py web` (opens http://localhost:8000/). Standard-library HTTP server, no new dependencies. Input: MP3/WAV upload or browser microphone. Shows the waveform (zoom/pan/seek, playback), the pitch track on a semitone grid with the key's notes shaded and in-key/out-of-key colouring, the snap target as a dashed line, a 12-note histogram of where the pitch lands, and a note/cents/target read-out. Dark theme by default, light toggle. | `dsfp_tune/web/server.py`, `dsfp_tune/web/static/` (`index.html`, `styles.css`, `app.js`, `mic-worklet.js`), `tune.py` (`web` subcommand), `README.md`, `tests/test_web.py` | not committed yet (Ullas commits) |
| Snap settings: mode `off` / `chromatic` / `key`, tonic (pitch class), scale. Saved by the page to `data/output/dashboard_settings.json`. Read them with `dsfp_tune.web.load_settings()` -> `SnapSettings(mode, tonic, scale)`, `.key` -> `Key` or None. | `dsfp_tune/web/settings.py` | not committed yet |

## Notes for other agents
- **Pitch correction is not implemented.** The page only *previews* the snap target (nearest integer MIDI note, or nearest in-key note) in JavaScript, for display. The real target-note logic (hysteresis, retune speed) and the pitch shifter are still unclaimed; when they exist, read the mode/key from `load_settings()`.
- Server routes: `GET /api/theory`, `GET|POST /api/settings`, `POST /api/analyze` (raw file body, `X-Filename` header -> `load_audio` + `yin_track`, 10 ms hop, 70-1000 Hz), `POST /api/live/start` + `POST /api/live/<id>` (raw float32 samples -> `YinStream` estimates) + `/api/live/<id>/stop`. A corrected-audio endpoint could be added next to `/api/analyze` later.
- The browser decodes the file itself for the waveform and playback; only pitch analysis runs in Python.
- Browser mic needs `localhost` (secure context). Bind with `--host 0.0.0.0` only if you accept other machines reaching it.
