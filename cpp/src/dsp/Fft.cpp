#include "dsp/Fft.h"

#include <cmath>
#include <stdexcept>
#include <utility>

namespace dsfp {

Fft::Fft(int size) : n_(size) {
    if (size < 2 || (size & (size - 1)) != 0)
        throw std::invalid_argument("FFT size must be a power of two >= 2");
    twiddle_.resize(static_cast<size_t>(n_ / 2));
    for (int k = 0; k < n_ / 2; ++k)
        twiddle_[static_cast<size_t>(k)] = std::polar(1.0, -2.0 * M_PI * k / n_);
    int bits = 0;
    while ((1 << bits) < n_) ++bits;
    bitrev_.resize(static_cast<size_t>(n_));
    for (int i = 0; i < n_; ++i) {
        int r = 0;
        for (int b = 0; b < bits; ++b)
            if (i & (1 << b)) r |= 1 << (bits - 1 - b);
        bitrev_[static_cast<size_t>(i)] = r;
    }
    buf_.resize(static_cast<size_t>(n_));
}

void Fft::transform(bool inverse) {
    for (int i = 0; i < n_; ++i) {
        const int j = bitrev_[static_cast<size_t>(i)];
        if (i < j) std::swap(buf_[static_cast<size_t>(i)], buf_[static_cast<size_t>(j)]);
    }
    for (int len = 2; len <= n_; len <<= 1) {
        const int half = len / 2, step = n_ / len;
        for (int i = 0; i < n_; i += len) {
            for (int j = 0; j < half; ++j) {
                std::complex<double> w = twiddle_[static_cast<size_t>(j * step)];
                if (inverse) w = std::conj(w);
                const std::complex<double> u = buf_[static_cast<size_t>(i + j)];
                const std::complex<double> v = buf_[static_cast<size_t>(i + j + half)] * w;
                buf_[static_cast<size_t>(i + j)] = u + v;
                buf_[static_cast<size_t>(i + j + half)] = u - v;
            }
        }
    }
}

void Fft::forwardReal(const double* in, std::complex<double>* out) {
    for (int i = 0; i < n_; ++i) buf_[static_cast<size_t>(i)] = {in[i], 0.0};
    transform(false);
    for (int k = 0; k <= n_ / 2; ++k) out[k] = buf_[static_cast<size_t>(k)];
}

void Fft::inverseReal(const std::complex<double>* in, double* out) {
    const int h = n_ / 2;
    buf_[0] = {in[0].real(), 0.0};
    buf_[static_cast<size_t>(h)] = {in[h].real(), 0.0};
    for (int k = 1; k < h; ++k) {
        buf_[static_cast<size_t>(k)] = in[k];
        buf_[static_cast<size_t>(n_ - k)] = std::conj(in[k]);
    }
    transform(true);
    for (int i = 0; i < n_; ++i) out[i] = buf_[static_cast<size_t>(i)].real() / n_;
}

}  // namespace dsfp
