// Streaming autotune: C++ port of dsfp_tune/correction.py (NoteSnapper, PitchCorrector).
//
//   input -> YinStream (f0) -> NoteSnapper (target note) -> attack/release smoothing -> FrequencyShifter
//
// Real-time safe: everything is allocated in the constructor; setParams() and processSample()
// never allocate. Chord harmony (Python `harmony=`) is not ported yet.
#pragma once

#include <array>
#include <atomic>
#include <memory>
#include <vector>

#include "dsp/FrequencyShifter.h"
#include "dsp/Yin.h"

namespace dsfp {

// Scales from dsfp_tune/theory/keys.py (semitone offsets from the tonic).
struct ScaleDef {
    const char* id;
    const char* label;
    std::vector<int> intervals;
};
const std::vector<ScaleDef>& scales();
// Pitch classes allowed by a key; chromatic = all twelve.
std::array<bool, 12> keyPitchClasses(int tonic, int scaleIndex);
std::array<bool, 12> chromaticPitchClasses();

// Settings that can change while running (all cheap to apply). Units match the Python arguments.
struct CorrectorParams {
    double retuneMs = 25.0;      // attack
    double releaseMs = 25.0;     // release (Python default: same as retune_ms)
    double amount = 1.0;         // 0..1
    double hysteresis = 0.15;    // semitones
    double holdMs = 60.0;
    double noteWindowMs = 120.0;
    double noteJump = 0.75;      // semitones
    double maxCorrection = 2.0;  // semitones
    double a4 = 440.0;
    std::array<bool, 12> allowed = chromaticPitchClasses();
};

// Snapshot of the latest pitch estimate, for displays.
struct CorrectorStatus {
    double f0 = 0.0;           // Hz, NaN when unvoiced
    bool voiced = false;
    double targetMidi = 0.0;   // NaN when not correcting
    double correction = 0.0;   // applied shift, semitones
    double ratio = 1.0;
    long estimateIndex = 0;    // number of estimates so far
};

class NoteSnapper {
public:
    void setAllowed(const std::array<bool, 12>& allowed);
    void setHysteresis(double h) { hysteresis_ = h; }
    int nearest(double midi) const;
    int update(double midi);  // target note for this frame (with hysteresis)
    void reset() { hasCurrent_ = false; }
    bool hasCurrent() const { return hasCurrent_; }
    int current() const { return current_; }

private:
    std::array<bool, 12> allowed_ = chromaticPitchClasses();
    double hysteresis_ = 0.15;
    bool hasCurrent_ = false;
    int current_ = 0;
};

class PitchCorrector {
public:
    // fMax is clamped to sampleRate/4. maxNoteWindowMs bounds noteWindowMs (sets a buffer size).
    PitchCorrector(double sampleRate, int nFft = 2048, int overlap = 4, int hop = 256, double fMin = 70.0,
                   double fMax = 1000.0, double threshold = 0.1, double maxNoteWindowMs = 1000.0);

    void setParams(const CorrectorParams& p);
    const CorrectorParams& params() const { return params_; }

    double processSample(double x);
    void process(const double* in, double* out, int n);
    void reset();

    int latency() const { return shifter_.latency(); }
    int hop() const { return hop_; }
    double sampleRate() const { return sampleRate_; }
    const CorrectorStatus& status() const { return status_; }  // read on the audio thread only

    // Called after every pitch estimate (offline tools record frames with it).
    void setEstimateCallback(void (*fn)(void*, const CorrectorStatus&), void* ctx) { cb_ = fn; cbCtx_ = ctx; }

private:
    void update(const PitchEstimate& est);
    double medianRecent();

    double sampleRate_;
    int hop_;
    YinStream yin_;
    FrequencyShifter shifter_;
    NoteSnapper snapper_;
    CorrectorParams params_;

    double dt_;
    double alphaAttack_ = 1.0, alphaRelease_ = 1.0;
    int holdFrames_ = 0;
    // Recent detected pitches (MIDI) for choosing the note: fixed-capacity ring.
    std::vector<double> recent_, sortScratch_;
    int recentCap_ = 1, recentHead_ = 0, recentCount_ = 0, recentLen_ = 1;

    int unvoicedRun_ = 0;
    double desired_ = 0.0, correction_ = 0.0, ratio_ = 1.0;
    CorrectorStatus status_;
    void (*cb_)(void*, const CorrectorStatus&) = nullptr;
    void* cbCtx_ = nullptr;
};

}  // namespace dsfp
