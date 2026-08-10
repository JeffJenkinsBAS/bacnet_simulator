"""Small psychrometric helpers used by the AHU and virtual-zone models.

The simulator does not need a full psychrometric chart package, but it does
need to conserve moisture correctly. Relative humidity is therefore converted
to humidity ratio before air streams are mixed or integrated. The equations
use standard sea-level pressure and are intentionally bounded to the normal
building-training range.
"""
from __future__ import annotations

from math import exp, log

STANDARD_PRESSURE_KPA = 101.325
MOLECULAR_WEIGHT_RATIO = 0.621945


def _saturation_pressure_kpa(temp_f: float) -> float:
    """Return saturation vapor pressure using the Magnus approximation."""
    temp_c = (float(temp_f) - 32.0) * 5.0 / 9.0
    temp_c = max(-40.0, min(80.0, temp_c))
    return 0.61078 * exp((17.2694 * temp_c) / (temp_c + 237.29))


def humidity_ratio_from_rh(temp_f: float, relative_humidity_pct: float) -> float:
    """Convert dry-bulb temperature and RH to lb water/lb dry air."""
    rh_fraction = max(0.0, min(1.0, float(relative_humidity_pct) / 100.0))
    vapor_pressure = rh_fraction * _saturation_pressure_kpa(temp_f)
    denominator = max(0.001, STANDARD_PRESSURE_KPA - vapor_pressure)
    return max(0.0, min(0.05, MOLECULAR_WEIGHT_RATIO * vapor_pressure / denominator))


def rh_from_humidity_ratio(temp_f: float, humidity_ratio: float) -> float:
    """Convert lb water/lb dry air to relative humidity percent."""
    ratio = max(0.0, min(0.05, float(humidity_ratio)))
    vapor_pressure = STANDARD_PRESSURE_KPA * ratio / (MOLECULAR_WEIGHT_RATIO + ratio)
    saturation_pressure = max(0.001, _saturation_pressure_kpa(temp_f))
    return max(0.0, min(100.0, 100.0 * vapor_pressure / saturation_pressure))


def moist_air_enthalpy_btu_per_lb(
    temp_f: float,
    relative_humidity_pct: float,
) -> float:
    """Return moist-air enthalpy in Btu/lb of dry air.

    The standard HVAC approximation is sufficiently accurate for economizer
    high-limit decisions at normal building temperatures.
    """
    dry_bulb = float(temp_f)
    humidity_ratio = humidity_ratio_from_rh(
        dry_bulb,
        relative_humidity_pct,
    )
    return 0.24 * dry_bulb + humidity_ratio * (1061.0 + 0.444 * dry_bulb)


def moist_air_enthalpy_from_humidity_ratio(
    temp_f: float,
    humidity_ratio: float,
) -> float:
    """Return moist-air enthalpy from dry-bulb and humidity ratio.

    Thermal equipment already carries humidity ratio internally because it is
    the conserved moisture quantity.  Keeping this form avoids a lossy
    humidity-ratio -> RH -> humidity-ratio round trip in mixing and coil
    energy balances.
    """
    dry_bulb = float(temp_f)
    ratio = max(0.0, min(0.05, float(humidity_ratio)))
    return 0.24 * dry_bulb + ratio * (1061.0 + 0.444 * dry_bulb)


def dry_bulb_from_enthalpy_and_humidity_ratio(
    enthalpy_btu_per_lb: float,
    humidity_ratio: float,
) -> float:
    """Recover dry-bulb temperature from moist-air enthalpy and ratio."""
    ratio = max(0.0, min(0.05, float(humidity_ratio)))
    denominator = max(0.01, 0.24 + 0.444 * ratio)
    return (float(enthalpy_btu_per_lb) - 1061.0 * ratio) / denominator


def dew_point_f(temp_f: float, relative_humidity_pct: float) -> float:
    """Return dew-point temperature in degrees Fahrenheit."""
    rh_fraction = max(0.0001, min(1.0, float(relative_humidity_pct) / 100.0))
    vapor_pressure_kpa = max(
        0.0001,
        rh_fraction * _saturation_pressure_kpa(temp_f),
    )
    alpha = log(vapor_pressure_kpa / 0.61078)
    dew_point_c = (237.29 * alpha) / (17.2694 - alpha)
    return dew_point_c * 9.0 / 5.0 + 32.0
