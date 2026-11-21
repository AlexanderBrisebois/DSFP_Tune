// Small radix-2 FFT (double precision) with numpy-compatible real transforms.
//
// Allocates only in the constructor, so forward/inverse are safe on an audio thread.
#pragma once

#include <complex>
#include <vector>

namespace dsfp {

class Fft {
public:
    explicit Fft(int size);  // size: power of two >= 2

    int size() const { return n_; }

    // numpy.fft.rfft: in[0..n) -> out[0..n/2]
    void forwardReal(const double* in, std::complex<double>* out);

    // numpy.fft.irfft: in[0..n/2] -> out[0..n). Imaginary parts of the DC and Nyquist bins are
    // ignored, as numpy does.
    void inverseReal(const std::complex<double>* in, double* out);

private:
    void transform(bool inverse);  // in place on buf_

    int n_;
    std::vector<std::complex<double>> twiddle_;  // exp(-2*pi*i*k/n), k < n/2
    std::vector<int> bitrev_;
    std::vector<std::complex<double>> buf_;
};

}  // namespace dsfp
