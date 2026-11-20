import numpy as np
import librosa as lr
from scipy.signal import ShortTimeFFT
from scipy.fft import fft, ifft, fftfreq, fftshift, ifftshift

def freq_shift(freq, power, f_fund, shift, sample_rate = 44100):
    # first we'll extract the power of the fundamental frequency
    sel = np.isclose(freq,f_fund,rtol=0.1)
    power_fund = power[sel]

    # then we will shift the power to the correct frequency
    sel = np.isclose(freq,f_fund*shift,rtol=0.1)
    power_new = power_fund

    # and we will perform an inverse FFT
    new_wave = ifft(power_new)

    # and now we return the new waveform

    return new_wave
    