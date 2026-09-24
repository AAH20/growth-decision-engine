"""Reproducible synthetic stress test of the published bootstrap estimator."""

from __future__ import annotations

import random

from .core import DataError, MAX_BOOTSTRAP_PICKS
from .stats import contribution_interval, wilson_interval


def _sample(rng: random.Random, count: int) -> list[int]:
    # Skewed contribution: $90 revenue on 18% accepted units, $12-$16
    # per-unit cost. No real customer records or external model are involved.
    return [(9000 if rng.random() < 0.18 else 0) - rng.randint(1200, 1600)
            for _ in range(count)]


def calibrate(*, replications: int = 100, units_per_arm: int = 80,
              resamples: int = 200, effect_cents: int = 300, seed: int = 1729) -> dict:
    """Estimate null false positives and known-effect power/coverage.

    This probes one declared synthetic distribution; the returned Wilson bands
    quantify Monte Carlo error, not external validity for customer data.
    """
    if not 20 <= replications <= 2000:
        raise DataError("replications must be between 20 and 2000")
    if not 10 <= units_per_arm <= 1000:
        raise DataError("units-per-arm must be between 10 and 1000")
    if not 100 <= resamples <= 10000:
        raise DataError("resamples must be between 100 and 10000")
    if not 1 <= effect_cents <= 10000:
        raise DataError("effect-cents must be between 1 and 10000")
    draws = 4 * replications * units_per_arm * resamples
    if draws > MAX_BOOTSTRAP_PICKS:
        raise DataError("calibration exceeds 20 million unit draws; reduce repetitions, units or resamples")
    rng = random.Random(seed)
    false_positives = coverage = detections = 0
    effect_usd = effect_cents / 100
    for _ in range(replications):
        null_control = _sample(rng, units_per_arm)
        null_treatment = _sample(rng, units_per_arm)
        low, high = contribution_interval(null_control, null_treatment,
                                          seed=rng.getrandbits(64), resamples=resamples)
        false_positives += low > 0 or high < 0

        effect_control = _sample(rng, units_per_arm)
        effect_treatment = [value + effect_cents for value in _sample(rng, units_per_arm)]
        low, high = contribution_interval(effect_control, effect_treatment,
                                          seed=rng.getrandbits(64), resamples=resamples)
        coverage += low <= effect_usd <= high
        detections += low > 0

    def result(successes: int) -> dict:
        band = wilson_interval(successes, replications)
        return {"count": successes, "rate": round(successes / replications, 4),
                "monte_carlo_95pct_wilson_interval": [round(value, 4) for value in band]}

    return {
        "protocol": "growth-decision-calibration/v1",
        "claim": "synthetic_single_distribution_diagnostic",
        "generator": "independent_units_18pct_acceptance_90usd_revenue_12_to_16usd_cost",
        "parameters": {"replications_per_scenario": replications, "units_per_arm": units_per_arm,
                       "resamples": resamples, "known_effect_usd_per_unit": effect_usd,
                       "seed": seed, "total_bootstrap_unit_draws": draws},
        "null_false_positive": result(false_positives),
        "known_effect_interval_coverage": result(coverage),
        "known_effect_positive_detection": result(detections),
        "limits": ["One synthetic distribution and sample size cannot establish field calibration.",
                   "Percentile bootstrap assumes independent units and fixed-horizon analysis.",
                   "Wilson intervals describe Monte Carlo uncertainty across replications only."],
    }
