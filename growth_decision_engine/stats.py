"""Deterministic statistics shared by scorecards and synthetic evaluation."""

from __future__ import annotations

import math
import random


def contribution_interval(control: list[int], treatment: list[int], *, seed: int, resamples: int) -> tuple[float, float]:
    """Within-arm percentile interval for treatment-minus-control USD/unit.

    Inputs are integer cents. This interval assumes independent randomized units;
    it does not adjust for repeated looks, clustering or assignment bias.
    """
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        control_mean = sum(rng.choice(control) for _ in control) / len(control)
        treatment_mean = sum(rng.choice(treatment) for _ in treatment) / len(treatment)
        draws.append(treatment_mean - control_mean)
    draws.sort()
    return (draws[math.floor(0.025 * (resamples - 1))] / 100,
            draws[math.ceil(0.975 * (resamples - 1))] / 100)


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    """95% Wilson interval for a Monte Carlo proportion (not a causal CI)."""
    z = 1.959963984540054
    p = successes / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * trials)) / trials) / denominator
    return (0.0 if successes == 0 else max(0.0, center - radius),
            1.0 if successes == trials else min(1.0, center + radius))
