#include "dsp/FrequencyShifter.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace dsfp {

namespace {
constexpr double kTwoPi = 6.283185307179586476925286766559;

// numpy.mod for a positive divisor: result in [0, m).
inline double pyMod(double a, double m) {
    double r = std::fmod(a, m);
    if (r < 0.0) r += m;
    return r;
}
}  // namespace

FrequencyShifter::FrequencyShifter(int nFft, int overlap)
    : nFft_(nFft), overlap_(overlap), hop_(0), nBins_(nFft / 2 + 1), olaNorm_(1.0), radPerBin_(0.0), fft_(nFft) {
    if (nFft < 16 || (nFft & (nFft - 1)) != 0) throw std::invalid_argument("nFft must be a power of two >= 16");
    if (overlap < 2 || nFft % overlap != 0) throw std::invalid_argument("overlap must be >= 2 and divide nFft");
    hop_ = nFft_ / overlap_;
    window_.resize(static_cast<size_t>(nFft_));
    double sumSq = 0.0;
    for (int i = 0; i < nFft_; ++i) {
        const double w = 0.5 - 0.5 * std::cos(kTwoPi * i / nFft_);  // periodic Hann
        window_[static_cast<size_t>(i)] = w;
        sumSq += w * w;
    }
    olaNorm_ = sumSq / hop_;
    radPerBin_ = kTwoPi * hop_ / nFft_;
    k_.resize(static_cast<size_t>(nBins_));
    for (int k = 0; k < nBins_; ++k) k_[static_cast<size_t>(k)] = k;

    const auto nb = static_cast<size_t>(nBins_);
    in_.assign(static_cast<size_t>(nFft_), 0.0);
    staging_.assign(static_cast<size_t>(hop_), 0.0);
    acc_.assign(static_cast<size_t>(nFft_), 0.0);
    frameBuf_.assign(static_cast<size_t>(nFft_), 0.0);
    mag_.assign(nb, 0.0);
    phase_.assign(nb, 0.0);
    lastPhase_.assign(nb, 0.0);
    sumPhase_.assign(nb, 0.0);
    trueBin_.assign(nb, 0.0);
    synMag_.assign(nb, 0.0);
    weighted_.assign(nb, 0.0);
    spec_.assign(nb, {0.0, 0.0});
    fifo_.assign(static_cast<size_t>(2 * hop_), 0.0);
    reset();
}

void FrequencyShifter::reset() {
    std::fill(in_.begin(), in_.end(), 0.0);
    std::fill(acc_.begin(), acc_.end(), 0.0);
    std::fill(lastPhase_.begin(), lastPhase_.end(), 0.0);
    std::fill(sumPhase_.begin(), sumPhase_.end(), 0.0);
    std::fill(fifo_.begin(), fifo_.end(), 0.0);
    since_ = 0;
    fifoRead_ = 0;
    fifoCount_ = hop_ - 1;  // hop-1 samples of silence: output length always matches input
}

void FrequencyShifter::frame(double ratio) {
    const auto nb = static_cast<size_t>(nBins_);
    for (int i = 0; i < nFft_; ++i)
        frameBuf_[static_cast<size_t>(i)] = in_[static_cast<size_t>(i)] * window_[static_cast<size_t>(i)];
    fft_.forwardReal(frameBuf_.data(), spec_.data());

    // Analysis: true frequency of each bin (in bins) from its phase advance since the last frame.
    for (size_t k = 0; k < nb; ++k) {
        const double re = spec_[k].real(), im = spec_[k].imag();
        mag_[k] = std::hypot(re, im);
        phase_[k] = std::atan2(im, re);
        double dev = phase_[k] - lastPhase_[k] - radPerBin_ * k_[k];
        dev -= kTwoPi * std::nearbyint(dev / kTwoPi);  // wrap to [-pi, pi] (numpy.round: half to even)
        trueBin_[k] = k_[k] + dev / radPerBin_;
        lastPhase_[k] = phase_[k];
    }

    if (ratio == 1.0) {
        // Pass-through: re-sync synthesis phase to the input so the output is exact.
        for (size_t k = 0; k < nb; ++k) {
            sumPhase_[k] = phase_[k];
            synMag_[k] = mag_[k];
        }
    } else {
        // Move bin k (and its true frequency) to bin round(k * ratio); collisions add power and
        // take the magnitude-weighted mean frequency.
        std::fill(synMag_.begin(), synMag_.end(), 0.0);
        std::fill(weighted_.begin(), weighted_.end(), 0.0);
        for (size_t k = 0; k < nb; ++k) {
            const double t = std::nearbyint(k_[k] * ratio);
            if (t < static_cast<double>(nBins_)) {
                const auto ti = static_cast<size_t>(t);
                synMag_[ti] += mag_[k];
                weighted_[ti] += mag_[k] * trueBin_[k] * ratio;
            }
        }
        for (size_t k = 0; k < nb; ++k) {
            const double synBin = synMag_[k] > 0.0 ? weighted_[k] / std::max(synMag_[k], 1e-30) : k_[k];
            sumPhase_[k] = pyMod(sumPhase_[k] + radPerBin_ * synBin, kTwoPi);
        }
    }

    for (size_t k = 0; k < nb; ++k) spec_[k] = std::polar(synMag_[k], sumPhase_[k]);
    fft_.inverseReal(spec_.data(), frameBuf_.data());
    for (int i = 0; i < nFft_; ++i)
        acc_[static_cast<size_t>(i)] += frameBuf_[static_cast<size_t>(i)] * window_[static_cast<size_t>(i)] / olaNorm_;

    // The first hop samples are complete: append them to the output FIFO, shift the accumulator.
    const int cap = static_cast<int>(fifo_.size());
    for (int i = 0; i < hop_; ++i)
        fifo_[static_cast<size_t>((fifoRead_ + fifoCount_ + i) % cap)] = acc_[static_cast<size_t>(i)];
    fifoCount_ += hop_;
    std::copy(acc_.begin() + hop_, acc_.end(), acc_.begin());
    std::fill(acc_.end() - hop_, acc_.end(), 0.0);
}

double FrequencyShifter::processSample(double x, double ratio) {
    staging_[static_cast<size_t>(since_++)] = x;
    if (since_ == hop_) {
        std::copy(in_.begin() + hop_, in_.end(), in_.begin());
        std::copy(staging_.begin(), staging_.end(), in_.end() - hop_);
        since_ = 0;
        frame(std::clamp(ratio, 0.25, 4.0));
    }
    const double y = fifo_[static_cast<size_t>(fifoRead_)];
    fifoRead_ = (fifoRead_ + 1) % static_cast<int>(fifo_.size());
    --fifoCount_;
    return y;
}

void FrequencyShifter::process(const double* in, double* out, int n, double ratio) {
    for (int i = 0; i < n; ++i) out[i] = processSample(in[i], ratio);
}

std::vector<double> freqShift(const std::vector<double>& x, double ratio, int nFft, int overlap) {
    FrequencyShifter s(nFft, overlap);
    const int lat = s.latency();
    std::vector<double> out(x.size());
    size_t o = 0;
    for (size_t i = 0; i < x.size() + static_cast<size_t>(lat); ++i) {
        const double y = s.processSample(i < x.size() ? x[i] : 0.0, ratio);
        if (i >= static_cast<size_t>(lat)) out[o++] = y;
    }
    return out;
}

}  // namespace dsfp
