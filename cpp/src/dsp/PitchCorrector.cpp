#include "dsp/PitchCorrector.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace dsfp {

namespace {
const double kNaN = std::numeric_limits<double>::quiet_NaN();
}

const std::vector<ScaleDef>& scales() {
    // Same order and intervals as dsfp_tune/theory/keys.py SCALES (minus duplicates ionian/aeolian
    // and chromatic, which the plugin offers as "Nearest note").
    static const std::vector<ScaleDef> s = {
        {"major", "Major", {0, 2, 4, 5, 7, 9, 11}},
        {"minor", "Minor", {0, 2, 3, 5, 7, 8, 10}},
        {"harmonic_minor", "Harmonic minor", {0, 2, 3, 5, 7, 8, 11}},
        {"melodic_minor", "Melodic minor", {0, 2, 3, 5, 7, 9, 11}},
        {"dorian", "Dorian", {0, 2, 3, 5, 7, 9, 10}},
        {"phrygian", "Phrygian", {0, 1, 3, 5, 7, 8, 10}},
        {"lydian", "Lydian", {0, 2, 4, 6, 7, 9, 11}},
        {"mixolydian", "Mixolydian", {0, 2, 4, 5, 7, 9, 10}},
        {"locrian", "Locrian", {0, 1, 3, 5, 6, 8, 10}},
        {"major_pentatonic", "Major pentatonic", {0, 2, 4, 7, 9}},
        {"minor_pentatonic", "Minor pentatonic", {0, 3, 5, 7, 10}},
        {"blues", "Blues", {0, 3, 5, 6, 7, 10}},
    };
    return s;
}

std::array<bool, 12> chromaticPitchClasses() {
    std::array<bool, 12> a{};
    a.fill(true);
    return a;
}

std::array<bool, 12> keyPitchClasses(int tonic, int scaleIndex) {
    const auto& all = scales();
    std::array<bool, 12> a{};
    if (scaleIndex < 0 || scaleIndex >= static_cast<int>(all.size())) return chromaticPitchClasses();
    for (int iv : all[static_cast<size_t>(scaleIndex)].intervals) a[static_cast<size_t>(((tonic + iv) % 12 + 12) % 12)] = true;
    return a;
}

// ---------------------------------------------------------------------------
// NoteSnapper
// ---------------------------------------------------------------------------
void NoteSnapper::setAllowed(const std::array<bool, 12>& allowed) {
    bool any = false;
    for (bool b : allowed) any = any || b;
    allowed_ = any ? allowed : chromaticPitchClasses();
    if (hasCurrent_ && !allowed_[static_cast<size_t>(((current_ % 12) + 12) % 12)]) hasCurrent_ = false;
}

int NoteSnapper::nearest(double midi) const {
    const int base = static_cast<int>(std::nearbyint(midi));  // Python round(): half to even
    int best = 0;
    double bestDist = std::numeric_limits<double>::infinity();
    for (int n = base - 6; n <= base + 6; ++n) {
        if (!allowed_[static_cast<size_t>(((n % 12) + 12) % 12)]) continue;
        const double d = std::abs(midi - n);
        if (d < bestDist) {  // ascending n: ties keep the lower note, like min(key=(dist, n))
            bestDist = d;
            best = n;
        }
    }
    return best;
}

int NoteSnapper::update(double midi) {
    const int best = nearest(midi);
    if (!hasCurrent_ || std::abs(midi - current_) > std::abs(midi - best) + hysteresis_) {
        current_ = best;
        hasCurrent_ = true;
    }
    return current_;
}

// ---------------------------------------------------------------------------
// PitchCorrector
// ---------------------------------------------------------------------------
PitchCorrector::PitchCorrector(double sampleRate, int nFft, int overlap, int hop, double fMin, double fMax,
                               double threshold, double maxNoteWindowMs)
    : sampleRate_(sampleRate),
      hop_(hop),
      yin_(sampleRate, hop, fMin, std::min(fMax, sampleRate / 4.0), threshold),
      shifter_(nFft, overlap),
      dt_(hop / sampleRate) {
    if (shifter_.hop() % hop_ != 0)
        throw std::invalid_argument("the shifter hop (nFft/overlap) must be a multiple of the pitch hop");
    recentCap_ = std::max(1, static_cast<int>(std::nearbyint(maxNoteWindowMs / 1000.0 / dt_)));
    recent_.assign(static_cast<size_t>(recentCap_), 0.0);
    sortScratch_.assign(static_cast<size_t>(recentCap_), 0.0);
    setParams(CorrectorParams{});
    status_.f0 = kNaN;
    status_.targetMidi = kNaN;
}

void PitchCorrector::setParams(const CorrectorParams& p) {
    params_ = p;
    params_.amount = std::clamp(p.amount, 0.0, 1.0);
    params_.retuneMs = std::max(0.0, p.retuneMs);
    params_.releaseMs = std::max(0.0, p.releaseMs);
    alphaAttack_ = params_.retuneMs == 0.0 ? 1.0 : 1.0 - std::exp(-dt_ * 1000.0 / params_.retuneMs);
    alphaRelease_ = params_.releaseMs == 0.0 ? 1.0 : 1.0 - std::exp(-dt_ * 1000.0 / params_.releaseMs);
    holdFrames_ = static_cast<int>(std::nearbyint(std::max(0.0, p.holdMs) / 1000.0 / dt_));
    recentLen_ = std::clamp(static_cast<int>(std::nearbyint(std::max(0.0, p.noteWindowMs) / 1000.0 / dt_)), 1, recentCap_);
    while (recentCount_ > recentLen_) {  // shrink: drop the oldest values
        recentHead_ = (recentHead_ + 1) % recentCap_;
        --recentCount_;
    }
    snapper_.setHysteresis(std::max(0.0, p.hysteresis));
    snapper_.setAllowed(p.allowed);
}

void PitchCorrector::reset() {
    yin_.reset();
    shifter_.reset();
    snapper_.reset();
    recentHead_ = recentCount_ = 0;
    unvoicedRun_ = 0;
    desired_ = correction_ = 0.0;
    ratio_ = 1.0;
    status_ = CorrectorStatus{};
    status_.f0 = kNaN;
    status_.targetMidi = kNaN;
}

double PitchCorrector::medianRecent() {
    for (int i = 0; i < recentCount_; ++i)
        sortScratch_[static_cast<size_t>(i)] = recent_[static_cast<size_t>((recentHead_ + i) % recentCap_)];
    auto begin = sortScratch_.begin(), end = begin + recentCount_;
    std::sort(begin, end);
    const int n = recentCount_;
    return n % 2 ? sortScratch_[static_cast<size_t>(n / 2)]
                 : 0.5 * (sortScratch_[static_cast<size_t>(n / 2 - 1)] + sortScratch_[static_cast<size_t>(n / 2)]);
}

void PitchCorrector::update(const PitchEstimate& est) {
    double desired, target;
    if (est.voiced) {
        const double midi = 69.0 + 12.0 * std::log2(est.f0 / params_.a4);
        if (snapper_.hasCurrent() && std::abs(midi - snapper_.current()) > params_.noteJump) {
            recentHead_ = recentCount_ = 0;  // a real note change: decide on the new pitch right away
        }
        // append to the note-choice window (drop the oldest when full)
        if (recentCount_ == recentLen_) {
            recentHead_ = (recentHead_ + 1) % recentCap_;
            --recentCount_;
        }
        recent_[static_cast<size_t>((recentHead_ + recentCount_) % recentCap_)] = midi;
        ++recentCount_;
        target = snapper_.update(medianRecent());
        desired = target - midi;
        if (std::abs(desired) > params_.maxCorrection) {  // likely an octave/pitch error: leave it alone
            desired = 0.0;
            target = kNaN;
        }
        desired *= params_.amount;
        unvoicedRun_ = 0;
    } else {
        ++unvoicedRun_;
        if (unvoicedRun_ > holdFrames_) {
            snapper_.reset();
            recentHead_ = recentCount_ = 0;
            desired = 0.0;
            target = kNaN;
        } else {  // brief dropout inside a note: hold the previous correction
            desired = desired_;
            target = snapper_.hasCurrent() ? snapper_.current() : kNaN;
        }
    }

    desired_ = desired;
    const double alpha = desired != 0.0 ? alphaAttack_ : alphaRelease_;  // attack toward a note, release to none
    correction_ += alpha * (desired - correction_);
    if (desired == 0.0 && std::abs(correction_) < 0.005) correction_ = 0.0;  // < 0.5 cent: bypass exactly
    ratio_ = std::pow(2.0, correction_ / 12.0);

    status_.f0 = est.f0;
    status_.voiced = est.voiced;
    status_.targetMidi = target;
    status_.correction = correction_;
    status_.ratio = ratio_;
    ++status_.estimateIndex;
    if (cb_) cb_(cbCtx_, status_);
}

double PitchCorrector::processSample(double x) {
    PitchEstimate est;
    if (yin_.push(x, est)) update(est);  // a new estimate sets the ratio for the frame ending here
    return shifter_.processSample(x, ratio_);
}

void PitchCorrector::process(const double* in, double* out, int n) {
    for (int i = 0; i < n; ++i) out[i] = processSample(in[i]);
}

}  // namespace dsfp
