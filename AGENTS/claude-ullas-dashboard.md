# Agent: Claude (working for Ullas): web dashboard

**Status:** waiting for Ullas's feedback
**Last updated:** 2026-11-20 16:00 PT

## Done
| What | Files | Commit |
|---|---|---|
| Web dashboard, `python tune.py web` (opens http://localhost:8000/). Standard-library HTTP server, no new Python dependencies. Input: MP3/WAV upload or browser microphone. Waveform (zoom/pan/seek, playback), pitch track on a semitone grid with the key's notes shaded and in-key/out-of-key colouring, snap target as a dashed line, 12-note histogram of where the pitch lands, note/cents/target read-out. Dark theme by default, light toggle. | `dsfp_tune/web/server.py`, `dsfp_tune/web/static/`, `tune.py` (`web`), `tests/test_web.py` | `e598fe3` |
| Snap settings: mode `off` / `chromatic` / `key`, tonic, scale, saved to `data/output/dashboard_settings.json`. Read with `dsfp_tune.web.load_settings()` -> `SnapSettings`. | `dsfp_tune/web/settings.py` | `e598fe3` |
| Public link: `python tune.py web --public` also runs a Cloudflare quick tunnel (`cloudflared tunnel --url http://127.0.0.1:<port>`), prints the random `https://...trycloudflare.com` link (new each run), and stops cloudflared when the server exits. Clear error if cloudflared is missing (`brew install cloudflared`). The server still listens on 127.0.0.1 only. | `dsfp_tune/web/tunnel.py`, `dsfp_tune/web/server.py` (`serve(public=...)`), `tune.py` (`--public`), `tests/test_tunnel.py` | committed with `5b6c4a1` |
| Spectrogram panel beside the pitch view: Hann STFT (4096-point, 512 hop) computed in the browser, rows on the same note axis as the pitch view, magma-like colours, thin detected-pitch line. Sources: uploaded file, live mic (from the ring buffer), and the last mic take, switchable Original / Autotuned (follows whichever is playing). | `dsfp_tune/web/static/spectrogram.js` (new), small hooks in `app.js`, `index.html`, `styles.css` | `947d973` |
| Playhead while a mic take plays back (original or autotuned): moving line on waveform, pitch view and spectrogram, the view follows it, read-out and note strip show the pitch at the playhead. The live pitch history is now kept for the whole take (up to 5 min). Files already had a playhead. | `dsfp_tune/web/static/app.js` | `947d973` |
| Spectrogram frequency axis fixed at 50 Hz - 8 kHz (log scale, Hz ticks); it no longer follows the pitch view's note range (Ullas didn't like the auto scaling). Playhead is clickable and draggable on the waveform, pitch view and spectrogram: on files grab the playhead to scrub (dragging elsewhere still pans), on mic takes click or drag anywhere; Play starts from there, and switching Original/Autotuned keeps the position. | `spectrogram.js`, `app.js` | `83b4e42` |
| Scrolling through time: scroll bars under the waveform, pitch view and spectrogram (drag the thumb, or click the track to jump), and wheel/trackpad scrolling on all three. All move the same window, so they stay in sync. Pinch or Ctrl+scroll zooms files. Files longer than 30 s open on a 20 s window; mic takes scroll in their 8 s window. | `dsfp_tune/web/static/scroll.js` (new), hooks in `app.js`, `index.html`, `styles.css` | this commit |

## Notes for other agents
- Anyone with the public link can use the dashboard: upload files, use the mic, and change the saved snap settings. There is no login.
- Server routes: `GET /api/theory`, `GET|POST /api/settings`, `POST /api/analyze`, `POST /api/live/start`, `POST /api/live/<id>`, `/api/live/<id>/stop` (others have since added autotune routes).
- Browser mic needs a secure context: `localhost` or the https tunnel link both work.
- Don't modify README.md (Ullas's rule).
