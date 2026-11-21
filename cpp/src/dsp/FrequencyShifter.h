// Streaming phase-vocoder frequency shifter: C++ port of dsfp_tune/synthesis/freq_shift.py.
//
// Moves every frequency f to f * ratio (pitch shift, same duration). Output equals the input
// delayed by latency() samples when ratio == 1. All buffers are allocated in the constructor.
#pragma once

#include <complex>
#include <vector>

#include "dsp/Fft.h"

namespace dsfp {

class FrequencyShifter {
public:
    explicit FrequencyShifter(int nFft = 2048, int overlap = 4);

    int nFft() const { return nFft_; }
    int hop() const { return hop_; }
    int latency() const { return nFft_ - 1; }  // samples from input to output

    // One sample in, one sample out. `ratio` (clamped to [0.25, 4]) applies to the frame that
    // completes on this sample, so it can change at any time.
    double processSample(double x, double ratio);
    void process(const double* in, double* out, int n, double ratio);
    void reset();

private:
    void frame(double ratio);

    int nFft_, overlap_, hop_, nBins_;
    double olaNorm_, radPerBin_;
    Fft fft_;
    std::vector<double> window_, in_, staging_, acc_, frameBuf_, k_;
    std::vector<double> mag_, phase_, lastPhase_, sumPhase_, trueBin_, synMag_, weighted_;
    std::vector<std::complex<double>> spec_;
    int since_ = 0;
    // Output FIFO: hop-1 samples of silence up front (as in Python), then each frame's hop samples.
    std::vector<double> fifo_;
    int fifoRead_ = 0, fifoCount_ = 0;
};

// Shift a whole signal; same length as the input, latency removed (Python freq_shift).
std::vector<double> freqShift(const std::vector<double>& x, double ratio, int nFft = 2048, int overlap = 4);

}  // namespace dsfp
