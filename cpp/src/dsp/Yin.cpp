#include "dsp/Yin.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace dsfp {

namespace {
const double kNaN = std::numeric_limits<double>::quiet_NaN();

int nextPow2(int n) {
    int p = 1;
    while (p < n) p <<= 1;
    return p;
}
}  // namespace

Yin::Yin(double sampleRate, double fMin, double fMax, int window, double threshold, double silenceDb)
    : sampleRate_(sampleRate),
      threshold_(threshold),
      silenceRms_(std::isnan(silenceDb) ? 0.0 : std::pow(10.0, silenceDb / 20.0)),
      silenceGate_(!std::isnan(silenceDb)),
      tauMin_(std::max(2, static_cast<int>(std::floor(sampleRate / fMax)))),
      tauMax_(static_cast<int>(std::ceil(sampleRate / fMin))),
      window_(window > 0 ? window : tauMax_),
      frameLength_(window_ + tauMax_),
      nFft_(nextPow2(frameLength_)),
      fft_(nFft_) {
    if (!(0.0 < fMin && fMin < fMax)) throw std::invalid_argument("need 0 < fMin < fMax");
    if (fMax > sampleRate / 4.0) throw std::invalid_argument("fMax too high for the sample rate (max sr/4)");
    if (!(0.0 < threshold && threshold < 1.0)) throw std::invalid_argument("threshold must be in (0, 1)");
    x_.assign(static_cast<size_t>(nFft_), 0.0);
    ref_.assign(static_cast<size_t>(nFft_), 0.0);
    r_.assign(static_cast<size_t>(nFft_), 0.0);
    csum_.assign(static_cast<size_t>(frameLength_ + 1), 0.0);
    d_.assign(static_cast<size_t>(tauMax_ + 1), 0.0);
    cmnd_.assign(static_cast<size_t>(tauMax_ + 1), 1.0);
    refSpec_.resize(static_cast<size_t>(nFft_ / 2 + 1));
    frameSpec_.resize(static_cast<size_t>(nFft_ / 2 + 1));
}

PitchEstimate Yin::detect(const double* frame) {
    const int L = frameLength_, W = window_, T = tauMax_;

    if (silenceGate_) {
        double ss = 0.0;
        for (int i = 0; i < L; ++i) ss += frame[i] * frame[i];
        if (std::sqrt(ss / L) < silenceRms_) return {kNaN, 1.0, false};
    }

    // Step 1: difference function d(tau) = E0 + E(tau) - 2 r(tau), r by FFT cross-correlation.
    std::fill(x_.begin(), x_.end(), 0.0);
    std::fill(ref_.begin(), ref_.end(), 0.0);
    std::copy(frame, frame + L, x_.begin());
    std::copy(frame, frame + W, ref_.begin());
    fft_.forwardReal(ref_.data(), refSpec_.data());
    fft_.forwardReal(x_.data(), frameSpec_.data());
    for (size_t k = 0; k < refSpec_.size(); ++k) refSpec_[k] = std::conj(refSpec_[k]) * frameSpec_[k];
    fft_.inverseReal(refSpec_.data(), r_.data());

    csum_[0] = 0.0;
    for (int i = 0; i < L; ++i) csum_[static_cast<size_t>(i + 1)] = csum_[static_cast<size_t>(i)] + x_[static_cast<size_t>(i)] * x_[static_cast<size_t>(i)];
    const double e0 = csum_[static_cast<size_t>(W)];
    for (int tau = 0; tau <= T; ++tau) {
        const double eTau = csum_[static_cast<size_t>(W + tau)] - csum_[static_cast<size_t>(tau)];
        d_[static_cast<size_t>(tau)] = std::max(e0 + eTau - 2.0 * r_[static_cast<size_t>(tau)], 0.0);
    }
    d_[0] = 0.0;

    // Step 2: cumulative mean normalised difference d'(tau).
    cmnd_[0] = 1.0;
    double running = 0.0;
    for (int tau = 1; tau <= T; ++tau) {
        running += d_[static_cast<size_t>(tau)];
        cmnd_[static_cast<size_t>(tau)] = running > 0.0 ? d_[static_cast<size_t>(tau)] * tau / running : 1.0;
    }

    // Step 3: absolute threshold (first dip below it, followed to its bottom).
    int tau = -1;
    bool found = false;
    for (int t = tauMin_; t <= T; ++t) {
        if (cmnd_[static_cast<size_t>(t)] < threshold_) {
            tau = t;
            found = true;
            break;
        }
    }
    if (found) {
        while (tau < T && cmnd_[static_cast<size_t>(tau + 1)] < cmnd_[static_cast<size_t>(tau)]) ++tau;
    } else {
        tau = tauMin_;
        for (int t = tauMin_ + 1; t <= T; ++t)
            if (cmnd_[static_cast<size_t>(t)] < cmnd_[static_cast<size_t>(tau)]) tau = t;
    }

    // Step 4: parabolic interpolation around the minimum.
    double tauExact = tau, aper = cmnd_[static_cast<size_t>(tau)];
    if (tau > 0 && tau < T) {
        const double a = cmnd_[static_cast<size_t>(tau - 1)], b = cmnd_[static_cast<size_t>(tau)],
                     c = cmnd_[static_cast<size_t>(tau + 1)];
        const double denom = a - 2.0 * b + c;
        if (denom > 0.0) {
            const double shift = 0.5 * (a - c) / denom;
            tauExact = tau + shift;
            aper = b - 0.25 * (a - c) * shift;
        }
    }
    aper = std::max(0.0, aper);
    if (!found) return {kNaN, aper, false};
    return {sampleRate_ / tauExact, aper, true};
}

YinStream::YinStream(double sampleRate, int hop, double fMin, double fMax, double threshold)
    : yin_(sampleRate, fMin, fMax, 0, threshold), hop_(hop) {
    if (hop < 1) throw std::invalid_argument("hop must be positive");
    buf_.assign(static_cast<size_t>(yin_.frameLength()), 0.0);
    staging_.assign(static_cast<size_t>(hop_), 0.0);
}

void YinStream::reset() {
    std::fill(buf_.begin(), buf_.end(), 0.0);
    since_ = 0;
}

bool YinStream::push(double sample, PitchEstimate& out) {
    staging_[static_cast<size_t>(since_++)] = sample;
    if (since_ < hop_) return false;
    since_ = 0;
    const size_t n = buf_.size(), h = static_cast<size_t>(hop_);
    if (h >= n) {
        std::copy(staging_.end() - static_cast<long>(n), staging_.end(), buf_.begin());
    } else {
        std::copy(buf_.begin() + static_cast<long>(h), buf_.end(), buf_.begin());
        std::copy(staging_.begin(), staging_.end(), buf_.end() - static_cast<long>(h));
    }
    out = yin_.detect(buf_.data());
    return true;
}

}  // namespace dsfp
