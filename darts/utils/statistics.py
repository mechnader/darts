"""
Utils for time series statistics
--------------------------------
"""

import math
from typing import Tuple, Optional, List, Union

import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import norm
from scipy.signal import argrelmax
from statsmodels.tsa.seasonal import seasonal_decompose
from statsmodels.tsa.stattools import acf, pacf, grangercausalitytests

from warnings import warn
from ..logging import raise_log, get_logger, raise_if_not, raise_if
from ..timeseries import TimeSeries
from .missing_values import fill_missing_values
from .utils import SeasonalityMode, ModelMode

logger = get_logger(__name__)


def check_seasonality(ts: TimeSeries,
                      m: Optional[int] = None,
                      max_lag: int = 24,
                      alpha: float = 0.05):
    """
    Checks whether the TimeSeries `ts` is seasonal with period `m` or not.

    If `m` is None, we work under the assumption that there is a unique seasonality period, which is inferred
    from the Auto-correlation Function (ACF).

    Parameters
    ----------
    ts
        The time series to check for seasonality.
    m
        The seasonality period to check.
    max_lag
        The maximal lag allowed in the ACF.
    alpha
        The desired confidence level (default 5%).

    Returns
    -------
    Tuple[bool, int]
        A tuple `(season, m)`, where season is a boolean indicating whether the series has seasonality or not
        and `m` is the seasonality period.
    """

    ts._assert_univariate()

    if m is not None and (m < 2 or not isinstance(m, int)):
        raise_log(ValueError('m must be an integer greater than 1.'), logger)

    if m is not None and m > max_lag:
        raise_log(ValueError('max_lag must be greater than or equal to m.'), logger)

    n_unique = np.unique(ts.values()).shape[0]

    if n_unique == 1:  # Check for non-constant TimeSeries
        return False, 0

    r = acf(ts.values(), nlags=max_lag, fft=False)  # In case user wants to check for seasonality higher than 24 steps.

    # Finds local maxima of Auto-Correlation Function
    candidates = argrelmax(r)[0]

    if len(candidates) == 0:
        logger.info('The ACF has no local maximum for m < max_lag = {}.'.format(max_lag))
        return False, 0

    if m is not None:
        # Check for local maximum when m is user defined.
        test = m not in candidates

        if test:
            return False, m

        candidates = [m]

    # Remove r[0], the auto-correlation at lag order 0, that introduces bias.
    r = r[1:]

    # The non-adjusted upper limit of the significance interval.
    band_upper = r.mean() + norm.ppf(1 - alpha / 2) * r.var()

    # Significance test, stops at first admissible value. The two '-1' below
    # compensate for the index change due to the restriction of the original r to r[1:].
    for candidate in candidates:
        stat = _bartlett_formula(r, candidate - 1, len(ts))
        if r[candidate - 1] > stat * band_upper:
            return True, candidate
    return False, 0


def _bartlett_formula(r: np.ndarray,
                      m: int,
                      length: int) -> float:
    """
    Computes the standard error of `r` at order `m` with respect to `length` according to Bartlett's formula.

    Parameters
    ----------
    r
        The array whose standard error is to be computed.
    m
        The order of the standard error.
    length
        The size of the underlying sample to be used.

    Returns
    -------
    float
        The standard error of `r` with order `m`.
    """

    if m == 1:
        return math.sqrt(1 / length)
    else:
        return math.sqrt((1 + 2 * sum(map(lambda x: x ** 2, r[:m - 1]))) / length)


def extract_trend_and_seasonality(ts: TimeSeries,
                                  freq: int = None,
                                  model: Union[SeasonalityMode, ModelMode] = ModelMode.MULTIPLICATIVE) -> \
        Tuple[TimeSeries, TimeSeries]:
    """
    Extracts trend and seasonality from a TimeSeries instance using `statsmodels.seasonal_decompose`.

    Parameters
    ----------
    ts
        The series to decompose
    freq
        The seasonality period to use.
    model
        The type of decomposition to use.
        Must be `from darts import ModelMode, SeasonalityMode` Enum member.
        Either MULTIPLICATIVE or ADDITIVE.
        Defaults ModelMode.MULTIPLICATIVE.

    Returns
    -------
        A tuple of (trend, seasonal) time series.
    """

    ts._assert_univariate()
    raise_if_not(model in ModelMode or model in SeasonalityMode,
                 "Unknown value for model_mode: {}.".format(model), logger)
    raise_if_not(model is not SeasonalityMode.NONE, "The model must be either MULTIPLICATIVE or ADDITIVE.")

    decomp = seasonal_decompose(ts.pd_series(), period=freq, model=model.value, extrapolate_trend='freq')

    season = TimeSeries.from_times_and_values(ts.time_index, decomp.seasonal)
    trend = TimeSeries.from_times_and_values(ts.time_index, decomp.trend)

    return trend, season


def remove_from_series(ts: TimeSeries,
                       other: TimeSeries,
                       model: Union[SeasonalityMode, ModelMode]) -> TimeSeries:
    """
    Removes the TimeSeries `other` from the TimeSeries `ts` as specified by `model`.
    Use e.g. to remove an additive or multiplicative trend from a series.

    Parameters
    ----------
    ts
        The TimeSeries to be modified.
    other
        The TimeSeries to remove.
    model
        The type of model considered.
        Must be `from darts import ModelMode, SeasonalityMode` Enums member.
        Either MULTIPLICATIVE or ADDITIVE.

    Returns
    -------
    TimeSeries
        A TimeSeries defined by removing `other` from `ts`.
    """

    ts._assert_univariate()
    raise_if_not(model in ModelMode or model in SeasonalityMode,
                 "Unknown value for model_mode: {}.".format(model), logger)

    if model.value == 'multiplicative':
        new_ts = ts / other
    elif model.value == 'additive':
        new_ts = ts - other
    else:
        raise_log(ValueError('Invalid parameter; must be either ADDITIVE or MULTIPLICATIVE. Was: {}'.format(model)))
    return new_ts


def remove_seasonality(ts: TimeSeries,
                       freq: int = None,
                       model: SeasonalityMode = SeasonalityMode.MULTIPLICATIVE) -> TimeSeries:
    """
    Adjusts the TimeSeries `ts` for a seasonality of order `frequency` using the `model` decomposition.

    Parameters
    ----------
    ts
        The TimeSeries to adjust.
    freq
        The seasonality period to use.
    model
        The type of decomposition to use.
        Must be a `from darts import SeasonalityMode` Enum member.
        Either SeasonalityMode.MULTIPLICATIVE or SeasonalityMode.ADDITIVE.
        Defaults SeasonalityMode.MULTIPLICATIVE.
    Returns
    -------
    TimeSeries
        A new TimeSeries instance that corresponds to the seasonality-adjusted 'ts'.
    """

    ts._assert_univariate()
    raise_if_not(model is not SeasonalityMode.NONE, "The model must be either MULTIPLICATIVE or ADDITIVE.")

    _, seasonality = extract_trend_and_seasonality(ts, freq, model)
    new_ts = remove_from_series(ts, seasonality, model)
    return new_ts


def remove_trend(ts: TimeSeries,
                 model: ModelMode = ModelMode.MULTIPLICATIVE) -> TimeSeries:
    """
    Adjusts the TimeSeries `ts` for a trend using the `model` decomposition.

    Parameters
    ----------
    ts
        The TimeSeries to adjust.
    model
        The type of decomposition to use.
        Must be `from darts import ModelMode` Enum member.
        Either ModelMode.MULTIPLICATIVE or ModelMode.ADDITIVE.
        Defaults to modelMode.MULTIPLICATIVE.
    Returns
    -------
    TimeSeries
        A new TimeSeries instance that corresponds to the trend-adjusted 'ts'.
    """

    ts._assert_univariate()

    trend, _ = extract_trend_and_seasonality(ts, model=model)
    new_ts = remove_from_series(ts, trend, model)
    return new_ts


def granger_causality_tests(ts_cause: TimeSeries,
                            ts_effect: TimeSeries,
                            maxlag: int,
                            addconst: bool = True,
                            verbose: bool = True
                          ) -> None:
    
    """
    Provides four tests for granger non causality of 2 time series using `statsmodels.tsa.stattools.grangercausalitytests`.


    Parameters
    ----------
    ts_cause
        A univariate deterministic time series. The statistical test determines if this time series 
        'Granger causes' the time series ts_effect (second parameter). Missing values are not supported.
        if H_0 (non causality) is rejected (p near 0), then there is a 'granger causality'.
    ts_effect
        Univariate time series 'Granger caused' by ts_cause.
    maxlag
        If an integer, computes the test for all lags up to maxlag. 
        If an iterable, computes the tests only for the lags in maxlag.
    addconst
        Include a constant in the model.
    verbose
        Print results.
    Returns
    -------
    Dict
        All test results, dictionary keys are the number of lags. For each lag the values are a tuple, 
        with the first element a dictionary with test statistic, pvalues, degrees of freedom, the second element are 
        the OLS estimation results for the restricted model, the unrestricted model and the restriction (contrast) 
        matrix for the parameter f_test.
    """

    ts_cause._assert_univariate()
    ts_effect._assert_univariate()

    ts_cause._assert_deterministic()
    ts_effect._assert_deterministic()

    raise_if(not ts_cause.has_same_time_as(ts_effect),
            'ts_cause and ts_effect time series have different time index.')



    return grangercausalitytests(
        np.concatenate((ts_effect.values(copy=False), ts_cause.values(copy=False)), axis=1), 
        maxlag,
        addconst,
        verbose
        )

def plot_acf(ts: TimeSeries,
             m: Optional[int] = None,
             max_lag: int = 24,
             alpha: float = 0.05,
             bartlett_confint: bool = True,
             fig_size: Tuple[int, int] = (10, 5),
             axis: Optional[plt.axis] = None) -> None:
    """
    Plots the ACF of `ts`, highlighting it at lag `m`, with corresponding significance interval.
    This function uses the `Statsmodels module <https://github.com/statsmodels/statsmodels>`_.

    Parameters
    ----------
    ts : TimeSeries
        The TimeSeries whose ACF should be plotted.
    m : int, optional
        Optionally, a time lag to highlight on the plot.
    max_lag : int, default: 24
        The maximal lag order to consider.
    alpha : float, default: 0.05
        The confidence interval to display.
    bartlett_confint : bool, default: True
        The boolean value indicating whether the confidence interval should be
        calculated using Bartlett's formula. If set to True, the confidence interval
        can be used in the model identification stage for fitting ARIMA models.
        If set to False, the confidence interval can be used to test for randomness
        (i.e. there is no time dependence in the data) of the data.
    fig_size : tuple of int, default: (10, 5)
        The size of the figure to be displayed.
    axis : plt.axis, optional
        Optionally, an axis object to plot the ACF on.
    """

    ts._assert_univariate()
    raise_if(max_lag is None or not (1 <= max_lag < len(ts)),
             'max_lag must be greater than or equal to 1 and less than len(ts).')
    raise_if(m is not None and not (0 <= m <= max_lag),
             'm must be greater than or equal to 0 and less than or equal to max_lag.')
    raise_if(alpha is None or not (0 < alpha < 1), 'alpha must be greater than 0 and less than 1.')

    r, confint = acf(ts.values(), nlags=max_lag, fft=False, alpha=alpha, bartlett_confint=bartlett_confint)

    if axis is None:
        plt.figure(figsize=fig_size)
        axis = plt

    for i in range(len(r)):
        axis.plot((i, i),
                  (0, r[i]),
                  color=('#b512b8' if m is not None and i == m else 'black'),
                  lw=(1 if m is not None and i == m else .5))

    # Adjusts the upper band of the confidence interval to center it on the x axis.
    upp_band = [confint[lag][1] - r[lag] for lag in range(1, max_lag + 1)]

    axis.fill_between(np.arange(1, max_lag + 1), upp_band, [-x for x in upp_band], color='#003DFD', alpha=.25)
    axis.plot((0, max_lag + 1), (0, 0), color='black')


def plot_pacf(ts: TimeSeries,
              m: Optional[int] = None,
              max_lag: int = 24,
              method: str = "ywadjusted",
              alpha: float = 0.05,
              fig_size: Tuple[int, int] = (10, 5),
              axis: Optional[plt.axis] = None) -> None:
    """
    Plots the Partial ACF of `ts`, highlighting it at lag `m`, with corresponding significance interval.
    This function uses the `Statsmodels module <https://github.com/statsmodels/statsmodels>`_.

    Parameters
    ----------
    ts : TimeSeries
        The TimeSeries whose ACF should be plotted.
    m : int, optional
        Optionally, a time lag to highlight on the plot.
    max_lag : int, default: 24
        The maximal lag order to consider.
    method : str, default: "ywadjusted"
        The method to be used for the PACF calculation.
        - "yw" or "ywadjusted" : Yule-Walker with sample-size adjustment in
          denominator for acovf. Default.
        - "ywm" or "ywmle" : Yule-Walker without adjustment.
        - "ols" : regression of time series on lags of it and on constant.
        - "ols-inefficient" : regression of time series on lags using a single
          common sample to estimate all pacf coefficients.
        - "ols-adjusted" : regression of time series on lags with a bias
          adjustment.
        - "ld" or "ldadjusted" : Levinson-Durbin recursion with bias
          correction.
        - "ldb" or "ldbiased" : Levinson-Durbin recursion without bias
          correction.
    alpha : float, default: 0.05
        The confidence interval to display.
    fig_size : tuple of int, default: (10, 5)
        The size of the figure to be displayed.
    axis : plt.axis, optional
        Optionally, an axis object to plot the ACF on.
    """

    ts._assert_univariate()
    raise_if(max_lag is None or not (1 <= max_lag < len(ts)//2),
             'max_lag must be greater than or equal to 1 and less than len(ts)//2.')
    raise_if(m is not None and not (0 <= m <= max_lag),
             'm must be greater than or equal to 0 and less than or equal to max_lag.')
    raise_if(alpha is None or not (0 < alpha < 1), 'alpha must be greater than 0 and less than 1.')

    r, confint = pacf(ts.values(), nlags=max_lag, method=method, alpha=alpha)

    if axis is None:
        plt.figure(figsize=fig_size)
        axis = plt

    for i in range(len(r)):
        axis.plot((i, i),
                  (0, r[i]),
                  color=('#b512b8' if m is not None and i == m else 'black'),
                  lw=(1 if m is not None and i == m else .5))

    # Adjusts the upper band of the confidence interval to center it on the x axis.
    upp_band = [confint[lag][1] - r[lag] for lag in range(1, max_lag + 1)]

    axis.fill_between(np.arange(1, max_lag + 1), upp_band, [-x for x in upp_band], color='#003DFD', alpha=.25)
    axis.plot((0, max_lag + 1), (0, 0), color='black')


def plot_hist(data: Union[TimeSeries, List[float], np.ndarray],
              bins: Optional[Union[int, np.ndarray, List[float]]] = None,
              density: bool = False,
              title: Optional[str] = None,
              fig_size: Optional[Tuple[int, int]] = None,
              ax: Optional[plt.axis] = None) -> None:
    """ This function plots the histogram of values in a TimeSeries instance or an array-like.

    All types of TimeSeries are supported (uni-, multivariate, deterministic, stochastic).
    Depending on the number of components in `data`, up to four histograms can be plotted on one figure.
    All stochastic samples will be displayed with the corresponding component.

    If `data` is an array-like, ALL values will be displayed in the same histogram.

    Parameters
    ----------
    data
        TimeSeries instance or an array-like from which to plot the histogram.
    bins
        Optionally, either an integer value for the number of bins to be displayed
        or an array-like of floats determining the position of bins.
    density
        bool, if `density` is set to True, the bin counts will be converted to probability density
    title
        The title of the figure to be displayed
    fig_size
        The size of the figure to be displayed.
    ax
        Optionally, an axis object to plot the histogram on.
    """

    n_plots_max = 4

    if isinstance(data, TimeSeries):
        if len(data.components) > n_plots_max:
            logger.warning("TimeSeries contains more than 4 components. Only the first 4 components will be displayed.")

        components = list(data.components[:n_plots_max])
        values = data[components].all_values(copy=False).flatten(order='F')
    else:
        values = data if isinstance(data, np.ndarray) else np.array(data)
        if len(values.shape) > 1:
            logger.warning("Input array-like data with `dim>1d` will be flattened and displayed in one histogram.")

        components = ['Data']
        values = values.flatten(order='F')

    # compute the number of columns and rows for subplots depending on shape of input data
    n_components = len(components)
    n_cols = 1 if n_components == 1 else 2
    n_rows = math.ceil(n_components / n_cols)

    title = 'Histogram' if title is None else title
    if ax is None:
        fig = plt.figure(constrained_layout=True, figsize=fig_size)
        gs = fig.add_gridspec(n_rows, n_cols)
        fig.suptitle(title)
        ax_all = [fig.add_subplot(gs[i]) for i in range(n_components)]
    else:
        if n_components > 1:
            logger.warning("Only the first component is plotted when calling plot_hist() with a given `ax`")
        ax.set_title(title)
        ax_all = [ax]

    n_entries = len(values) // n_components
    for i, label, ax_i in zip(range(n_components), components, ax_all):
        ax_i.hist(values[i * n_entries:(i + 1) * n_entries], bins=bins, density=density, label=label)
        ax_i.set_xlabel('value')
        ax_i.set_ylabel('count' if not density else 'probability density')
        ax_i.legend()


def plot_residuals_analysis(residuals: TimeSeries,
                            num_bins: int = 20,
                            fill_nan: bool = True) -> None:
    """ Plots data relevant to residuals.

    This function takes a univariate TimeSeries instance of residuals and plots their values,
    their distribution and their ACF.
    Please note that if the residual TimeSeries instance contains NaN values, the plots
    might be displayed incorrectly. If `fill_nan` is set to True, the missing values will
    be interpolated.

    Parameters
    ----------
    residuals
        Univariate TimeSeries instance representing residuals.
    num_bins
        Optionally, an integer value determining the number of bins in the histogram.
    fill_nan:
        A boolean value indicating whether NaN values should be filled in the residuals.
    """

    residuals._assert_univariate()

    fig = plt.figure(constrained_layout=True, figsize=(8, 6))
    gs = fig.add_gridspec(2, 2)

    if fill_nan:
        residuals = fill_missing_values(residuals)

    # plot values
    ax1 = fig.add_subplot(gs[:1, :])
    residuals.plot(ax=ax1)
    ax1.set_ylabel('value')
    ax1.set_title('Residual values')

    # plot histogram and distribution
    res_mean, res_std = np.mean(residuals.univariate_values()), np.std(residuals.univariate_values())
    res_min, res_max = min(residuals.univariate_values()), max(residuals.univariate_values())
    x = np.linspace(res_min, res_max, 100)
    ax2 = fig.add_subplot(gs[1:, 1:])
    plot_hist(residuals, bins=num_bins, ax=ax2)
    ax2.plot(x, norm(res_mean, res_std).pdf(x) * len(residuals) * (res_max - res_min) / num_bins)
    ax2.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax2.set_title('Distribution')
    ax2.set_ylabel('count')
    ax2.set_xlabel('value')

    # plot ACF
    ax3 = fig.add_subplot(gs[1:, :1])
    plot_acf(residuals, axis=ax3)
    ax3.set_ylabel('ACF value')
    ax3.set_xlabel('lag')
    ax3.set_title('ACF')
