import numpy as np
import librosa as lr
from scipy.signal import ShortTimeFFT
from scipy.fft import fft, ifft, fftfreq, fftshift, ifftshift

def freq_shift(freq, power, f_fund, shift, sample_rate = 44100):
    # first we'll extract the power of the fundamental frequency
    sel = (freq == f_fund)
    power_fund = power[sel]

    # then we will shift the power to the correct frequency
    sel = (freq == f_fund*shift)
    power_new = power_fund

    # and we will perform an inverse FFT
    new_wave = ifft(power_new)

    # and now we return the new waveform

    return new_wave
    