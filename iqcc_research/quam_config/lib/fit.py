from matplotlib import pyplot as plt
import matplotlib
from iqcc_research.quam_config.lib import guess
from scipy.optimize import curve_fit
import numpy as np
import xarray as xr
from lmfit import Model, Parameter, Parameters
from scipy.signal import find_peaks, peak_widths
import scipy.sparse as sparse
from scipy.sparse.linalg import spsolve
from scipy.fft import fft


def fix_initial_value(x, da):
    if len(da.dims) == 1:
        return float(x)
    else:
        return x








def echo_decay_exp(t, a, offset, decay, decay_echo):
    return a * np.exp(-t * decay - (t * decay_echo) ** 2) + offset
    # return a * np.exp(-t * decay) + offset


def fit_echo_decay_exp(da, dim):
    def get_decay(dat):
        def oed(d):
            return guess.oscillation_exp_decay(da[dim], d)

        return np.apply_along_axis(oed, -1, dat)

    def get_amp(dat):
        max_ = np.max(dat, axis=-1)
        min_ = np.min(dat, axis=-1)
        return (max_ - min_) / 2

    decay_guess = xr.apply_ufunc(get_decay, da, input_core_dims=[[dim]]).rename("decay guess")
    amp_guess = xr.apply_ufunc(get_amp, da, input_core_dims=[[dim]]).rename("amp guess")

    def apply_fit(x, y, a, offset, decay, decay_echo):
        try:
            fit = curve_fit(echo_decay_exp, x, y, p0=[a, offset, decay, decay_echo])[0]
            return fit
        except RuntimeError as e:
            print(f"{a=}, {offset=}, {decay=}, {decay_echo=}")
            plt.plot(x, echo_decay_exp(x, a, offset, decay, decay_echo))
            plt.plot(x, y)
            plt.show()
            # raise e

    fit_res = xr.apply_ufunc(
        apply_fit,
        da[dim],
        da,
        amp_guess,
        -0.0005,
        decay_guess,
        decay_guess,
        input_core_dims=[[dim], [dim], [], [], [], []],
        output_core_dims=[["fit_vals"]],
        vectorize=True,
    )
    return fit_res.assign_coords(fit_vals=("fit_vals", ["a", "offset", "decay", "decay_echo"]))





def fix_oscillation_phi_2pi(fit_data):
    """
    A specific helper function for a dataset that is returned by `fit_oscillation`.

    This function is used to fix sign problems in amp and f fit results (not relevant anymore)
    and also to "wrap" problematic points around 2pi. (Should be solved differently by not using phase in fit directly)
    TODO: remove this function. We keep in temporarily for backwards compatiblity.
    """
    phase = fit_data.sel(fit_vals="phi") * np.sign(fit_data.sel(fit_vals="f"))
    phase = phase.where(np.sign(fit_data.sel(fit_vals="a")) == 1, phase - np.pi)
    phase = ((phase + 1) % (2 * np.pi) - 1) / (2 * np.pi)
    return phase





def extract_dominant_frequencies(da, dim="idle_time"):
    def extract_dominant_frequency(signal, sample_rate):
        fft_result = fft(signal)
        frequencies = np.fft.fftfreq(len(signal), 1 / sample_rate)
        positive_freq_idx = np.where(frequencies > 0)
        dominant_idx = np.argmax(np.abs(fft_result[positive_freq_idx]))
        return frequencies[positive_freq_idx][dominant_idx]

    def extract_dominant_frequency_wrapper(signal):
        sample_rate = 1 / (da.coords[dim][1].values - da.coords[dim][0].values)  # Assuming uniform sampling
        return extract_dominant_frequency(signal, sample_rate)

    dominant_frequencies = xr.apply_ufunc(
        extract_dominant_frequency_wrapper, da, input_core_dims=[[dim]], output_core_dims=[[]], vectorize=True
    )

    return dominant_frequencies
