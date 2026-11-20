# Agent: Claude (working for Alex): pitch detection, microphone, plots, autotune pipeline, dashboard playback

**Status:** idle, waiting for the next task from Alex
**Last updated:** 2026-11-20 15:14 PT

## Done
| What | Files | Commit |
|---|---|---|
| YIN pitch (f0) detector. The difference function is computed with FFTs (identical to the direct formula, about 24× faster). `Yin.detect(frame)` gives `PitchEstimate(f0, aperiodicity, voiced)`; `yin_track()` covers a whole file. Has a silence gate and sub-sample (parabolic) refinement | `dsfp_tune/analysis/yin.py`, `tests/test_yin.py` | `9feaa10` |
| Streaming YIN: `YinStream.push(block)` takes blocks of any size and returns an estimate every `hop_length` samples | `dsfp_tune/analysis/yin.py` | `414dc06` |
| Microphone input (`sounddevice`): `record()`, `MicrophoneStream` (callback/queue mode, or polling mode with `use_callback=False`), `list_input_devices()` | `dsfp_tune/io/microphone.py`, `tests/test_microphone.py` | `414dc06` |
| `save_wav()` (16-bit PCM), plus a stdlib `wave` fallback in `load_audio` so WAV files load without ffmpeg or soundfile | `dsfp_tune/io/audio_io.py` | `414dc06` |
| Pitch plots: `plot_pitch()` (waveform, spectrogram with f0 line, cents off the held note) and `LivePitchPlot` (scrolling live window from the microphone) | `dsfp_tune/plotting.py`, `tests/test_plotting.py` | `414dc06` |
| Merged into Ullas's restructure: moved the work above into `dsfp_tune/`; turned the CLI into `tune.py` subcommands `live`, `record`, `pitch`, `devices`; dropped `pyproject.toml`; added `sounddevice` to `requirements.txt`; combined the READMEs | `tune.py`, `README.md`, `requirements.txt` | `8554d32` |
| Added `nearest_note(freq) -> (name, cents)` to the theory module and removed a duplicate note-helper module I had in `analysis/`. All note/frequency conversion now goes through `dsfp_tune/theory` | `dsfp_tune/theory/notes.py`, `dsfp_tune/theory/__init__.py` | `8554d32` |
| **FFT frequency shifter** (streaming phase vocoder): `FrequencyShifter.process(block, ratio)` and `freq_shift(x, ratio)`. Builds on Guinevere's `scripts/freq_shift.py` idea (move power at f to f × shift, then inverse FFT), adding STFT frames, shifting all harmonics, and phase tracking. Exact pass-through at ratio 1; tested within 2 cents for −3 to +12 semitones; 0.1 ms per 512-sample hop | `dsfp_tune/synthesis/freq_shift.py`, `tests/test_correction.py` | `1f8dcb1` |
| **Autotune pipeline**: `PitchCorrector` (streaming: YIN → `NoteSnapper` target note, chromatic or any `theory.Key` → retune smoothing → shifter) and `correct_pitch()` (offline). Includes hysteresis, a 120 ms median for note choice (vibrato doesn't flip notes), a hold through short unvoiced gaps, and `max_correction` against octave errors | `dsfp_tune/correction.py`, `tests/test_correction.py` | `1f8dcb1` |
| Real-time duplex audio (`DuplexStream`: mic → process → output) and `play()` | `dsfp_tune/io/microphone.py` | `1f8dcb1` |
| Before/after plot `plot_correction()` (sung vs target vs corrected on a note grid, cents off target) | `dsfp_tune/plotting.py` | `1f8dcb1` |
| CLI `python tune.py autotune [file] [--key "A minor"] [--retune-ms N] [--live]`: records (or loads), corrects, saves `data/output/<name>_tuned.wav`, plays original then corrected, plots. `--live` refuses to run on built-in speakers (feedback risk) without `--allow-speakers` | `tune.py`, README | `1f8dcb1` |
| **Dashboard: autotuned playback of microphone takes.** The page keeps the whole take while the mic runs (up to 5 min). On Stop, a "Last recording" panel offers ▶ Original / ▶ Autotuned, a retune-speed slider and WAV downloads. The new `POST /api/autotune?sample_rate=&retune_ms=&mode=&tonic=&scale=` takes raw float32 and returns a corrected WAV (summary JSON in the `X-Autotune-Summary` header), via `autotune_samples()` → `correct_pitch()`. It uses the page's snap settings, and changing them or the slider re-processes the take. Verified end to end in headless Chromium with a fake-mic WAV: notes at +36/−39/+31¢ came back within ±1¢ | `dsfp_tune/web/server.py`, `dsfp_tune/web/static/{index.html,app.js,styles.css}`, `dsfp_tune/io/audio_io.py` (`wav_bytes`), `tests/test_web_autotune.py`, README | this commit |

## In progress
Nothing.

## Likely next (not started)
- Try `python tune.py autotune` on real singing; tune the defaults (`retune_ms`, hysteresis, YIN threshold).
- Sound quality: phase locking in the shifter (less "phasey"), and formant preservation via spectral envelope.
- Live latency is about 120 ms on the MacBook's built-in mic: roughly 95 ms of driver buffering plus 23 ms shifter frame. A USB interface or smaller frames would help.

## Notes for other agents
- **Ullas: I edited your dashboard files** to add autotuned playback. Changes:
  - `server.py`: a new `/api/autotune` route and `autotune_samples()`; `_send()` takes optional extra headers.
  - `index.html`: a recording panel in the Microphone pane. The Snapping hint no longer says correction isn't built.
  - `styles.css`: rules appended at the end (`.recording`, `.secondary-button`, `.retune`).
  - `app.js`: `finishRecording()`, `autotuneRecording()`, `playRecording()`, `wavBlob()`; `startMic`/`stopMic` collect the take; `settingsChanged()` re-runs autotune.

  Not touched: the `settings.py` docstring still says "Pitch correction isn't built yet".
- **File mode has no autotuned playback yet.** `/api/autotune` takes raw samples, so the page could send the decoded file the same way.
- **Guinevere's `scripts/freq_shift.py` (as of `403f139`) was left untouched.** As written, it returns `ifft` of only the bins near f0: given 1 s of audio it returns 45 complex samples, the second `sel` is unused, and nothing moves to `f0*shift`. So the pipeline uses `dsfp_tune/synthesis/freq_shift.py`, which implements the same idea. Guinevere, compare the two, or swap in your version behind the same `process(block, ratio)` interface.
- **Target-note logic now exists:** `dsfp_tune/correction.py`. It *reads* `dsfp_tune/theory` (`Key`, `parse_key`, `freq_to_midi`) and doesn't change it. Ullas had held off on key snapping; this is where it lives now.
- **Microphone inside GUI windows:** matplotlib's macOS window loop holds the GIL and starves PortAudio callback threads, so no audio arrives. Use `MicrophoneStream(use_callback=False)` and read from a GUI timer, as `LivePitchPlot.run()` does. Real-time correction (`DuplexStream`) runs without a GUI for this reason.
- **Plots in tests:** `tests/conftest.py` sets the Agg matplotlib backend, so tests never open windows.
- **Hardware in tests:** fake `sounddevice` modules in `tests/test_microphone.py` and `tests/test_correction.py` stand in for real devices.
