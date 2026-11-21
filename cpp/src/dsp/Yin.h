// YIN pitch detector: C++ port of dsfp_tune/analysis/yin.py (Yin, YinStream).
//
// Same algorithm, defaults and edge cases as the Python reference; tests/test_cpp_port.py
// checks they agree. All buffers are allocated in the constructor.
#pragma once

#include <complex>
#include <vector>

#include "dsp/Fft.h"

namespace dsfp {

struct PitchEstimate {
    double f0 = 0.0;            // Hz; NaN when unvoiced
    double aperiodicity = 1.0;  // d' at the chosen lag: ~0 periodic, ~1 noise
    bool voiced = false;
};

class Yin {
public:
    // silenceDb: frames quieter than this (dBFS RMS) are unvoiced; pass NaN to disable.
    Yin(double sampleRate, double fMin = 70.0, double fMax = 1000.0, int window = 0, double threshold = 0.1,
        double silenceDb = -60.0);

    int frameLength() const { return frameLength_; }
    int tauMin() const { return tauMin_; }
    int tauMax() const { return tauMax_; }

    // Estimate f0 for frame[0..frameLength()).
    PitchEstimate detect(const double* frame);

private:
    double sampleRate_, threshold_, silenceRms_;
    bool silenceGate_;
    int tauMin_, tauMax_, window_, frameLength_, nFft_;
    Fft fft_;
    std::vector<double> x_, ref_, r_, csum_, d_, cmnd_;
    std::vector<std::complex<double>> refSpec_, frameSpec_;
};

// Feed samples one at a time; an estimate is produced every `hop` samples on the most recent
// frameLength() samples (initially silence), like the Python YinStream.
class YinStream {
public:
    YinStream(double sampleRate, int hop = 256, double fMin = 70.0, double fMax = 1000.0, double threshold = 0.1);

    // Returns true (and fills `out`) when this sample completes a hop.
    bool push(double sample, PitchEstimate& out);
    void reset();

    const Yin& yin() const { return yin_; }
    int hop() const { return hop_; }

private:
    Yin yin_;
    int hop_;
    int since_ = 0;
    std::vector<double> buf_, staging_;
};

}  // namespace dsfp
