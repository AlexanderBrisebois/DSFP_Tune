# Agent: Claude (working for Ullas): FFT peak pitch detection (second mode next to YIN)

**Status:** done, committed locally, waiting for Ullas to push
**Last updated:** 2026-11-20 16:45 PT

## Done
| What | Files | Commit |
|---|---|---|
| `FftPitch`: pitch read straight off the Fourier transform. Hann window, zero-padded rFFT, **largest** magnitude peak between `f_min` and `f_max`, parabolic (quadratic) interpolation of the peak on the dB magnitudes for sub-bin accuracy. Voiced/unvoiced: RMS silence gate (-60 dBFS) plus peak prominence: `aperiodicity` = median bin / peak in the band, voiced if < `threshold` (0.1 = peak >= 20 dB above the median). Frame = 4 periods of `f_min` (2520 samples / 57 ms at 44.1 kHz, vs YIN's 29 ms), FFT 8192, bins 5.4 Hz. Same `detect() -> PitchEstimate`, `frame_length`, `latency_seconds` as `Yin`. Pure NumPy (no C++ version) | `dsfp_tune/analysis/fft_pitch.py` (new) | this commit |
| `make_pitch_detector("yin" \| "fft", sr, ...)`; `method=` on `YinStream`, `yin_track`, `HeldPitchStream` (so pitch hold works with both); `pitch_method=` on `PitchCorrector` / `correct_pitch` (snapping + chord harmony). Default stays `"yin"` everywhere | `analysis/yin.py` (small), `analysis/pitch_hold.py` (doc), `analysis/__init__.py`, `correction.py` | this commit |
| CLI: `--pitch yin\|fft` on `pitch`, `record`, `live`, `autotune` (incl. `--live`); plots label the line "FFT f0" | `tune.py`, `plotting.py` (`method=` on `plot_pitch`, `plot_correction`, `LivePitchPlot`) | this commit |
| Dashboard: "Pitch detection: YIN / FFT peak" switch in the Input card. Saved as `SnapSettings.pitch_method`. Switching re-analyses the loaded file, moves a running microphone to a new live session with the other detector, and re-runs the autotune. `/api/analyze?pitch_method=`, `/api/live/start {"pitch_method"}`, `/api/autotune?pitch_method=` (all fall back to the saved setting). Verified in headless Chromium: no console errors | `web/settings.py`, `web/server.py`, `web/static/{index.html,app.js,filetune.js,styles.css}` | this commit |
| Tests: accuracy on sines (< 3 cents, 72-990 Hz), interpolation between bins, noise/silence unvoiced, weak-fundamental case (FFT gives the 2nd harmonic, YIN the fundamental), streaming/hold/autotune+harmony with `fft`, web endpoints. Updated `test_web.py::test_settings_endpoint` for the new setting. Full suite: 213 passed, 50 skipped (C++ not built) | `tests/test_fft_pitch.py`, `tests/test_web_pitch_method.py` (new), `tests/test_web.py` | this commit |

## Findings (11 clips in data/audio/singing, frames where both detectors say voiced)
- FFT agrees with YIN (within 50 cents) on 75% of frames; 12% are an octave up, 8% a twelfth (3x) up: the 2nd/3rd harmonic is louder than the fundamental. Clips with strong fundamentals (humming, kid, 11_female_day) agree 97-100%; 06 (scale) 8% and 07 (male) 24%.
- FFT says "voiced" more often than YIN (its prominence test is looser, e.g. 74% vs 10% on clip 01).
- Fixes if wanted later (not done, they change the method): harmonic product spectrum, cepstrum, or "lowest peak within N dB of the largest".

## Notes for other agents
- `YinStream.yin` / `HeldPitchStream.yin` / `PitchCorrector.yin.yin` may now hold an `FftPitch`; use only `detect`, `frame_length`, `latency_seconds` on it.
