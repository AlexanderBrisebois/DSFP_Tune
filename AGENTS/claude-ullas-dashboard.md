# Agent: Claude (working for Ullas): web dashboard

**Status:** waiting for Ullas's feedback
**Last updated:** 2026-11-20 15:30 PT

## Done
| What | Files | Commit |
|---|---|---|
| Web dashboard, `python tune.py web` (opens http://localhost:8000/). Standard-library HTTP server, no new Python dependencies. Input: MP3/WAV upload or browser microphone. Waveform (zoom/pan/seek, playback), pitch track on a semitone grid with the key's notes shaded and in-key/out-of-key colouring, snap target as a dashed line, 12-note histogram of where the pitch lands, note/cents/target read-out. Dark theme by default, light toggle. | `dsfp_tune/web/server.py`, `dsfp_tune/web/static/`, `tune.py` (`web`), `tests/test_web.py` | `e598fe3` |
| Snap settings: mode `off` / `chromatic` / `key`, tonic, scale, saved to `data/output/dashboard_settings.json`. Read with `dsfp_tune.web.load_settings()` -> `SnapSettings`. | `dsfp_tune/web/settings.py` | `e598fe3` |
| Public link: `python tune.py web --public` also runs a Cloudflare quick tunnel (`cloudflared tunnel --url http://127.0.0.1:<port>`), prints the random `https://...trycloudflare.com` link (new each run), and stops cloudflared when the server exits. Clear error if cloudflared is missing (`brew install cloudflared`). The server still listens on 127.0.0.1 only. | `dsfp_tune/web/tunnel.py`, `dsfp_tune/web/server.py` (`serve(public=...)`), `tune.py` (`--public`), `tests/test_tunnel.py` | not committed yet (Ullas commits) |

## Notes for other agents
- Anyone with the public link can use the dashboard: upload files, use the mic, and change the saved snap settings. There is no login.
- Server routes: `GET /api/theory`, `GET|POST /api/settings`, `POST /api/analyze`, `POST /api/live/start`, `POST /api/live/<id>`, `/api/live/<id>/stop` (others have since added autotune routes).
- Browser mic needs a secure context: `localhost` or the https tunnel link both work.
- Don't modify README.md (Ullas's rule).
