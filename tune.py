"""Pick the adaptive decoder's learning settings by trying a grid of them.

Run it with:
    python tune.py

To keep the benchmark honest, tuning uses DIFFERENT random seeds (100-102)
from the ones benchmark.py reports on (0-4). Otherwise the settings could be
fitted to the exact noise in the reported numbers.

The score is the average character error rate over every condition in every
benchmark sweep, for both the no-setup and the calibrated decoder.
"""

import itertools
import statistics

from benchmark import DEFAULT, SWEEPS
from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.calibration import CalibrationError, calibrate
from morse_aac.metrics import character_error_rate
from morse_aac.synth import PHRASES, synthesize

TUNING_SEEDS = range(100, 103)

GRID = {
    "speed_drift": [0.001, 0.002, 0.004, 0.008],
    "stretch_drift": [0.0001, 0.0005, 0.002],
    "initial_uncertainty": [0.005, 0.02],
}


def score(settings: dict) -> float:
    errors = []
    for _sweep, (_label, values, apply) in SWEEPS.items():
        for value in values:
            sender = apply(DEFAULT, value)
            for phrase in PHRASES:
                for seed in TUNING_SEEDS:
                    events = synthesize(phrase, sender, seed=seed)
                    errors.append(character_error_rate(
                        phrase, AdaptiveDecoder(**settings).decode(events)))
                    try:
                        timing = calibrate(synthesize("PARIS", sender, seed=20_000 + seed))
                    except CalibrationError:
                        timing = None
                    errors.append(character_error_rate(
                        phrase, AdaptiveDecoder(timing, **settings).decode(events)))
    return statistics.mean(errors)


def main() -> None:
    keys = list(GRID)
    results = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        settings = dict(zip(keys, combo, strict=True))
        s = score(settings)
        results.append((s, settings))
        print(f"{s:7.2%}  {settings}")
    results.sort(key=lambda r: r[0])
    print("\nBest five:")
    for s, settings in results[:5]:
        print(f"{s:7.2%}  {settings}")


if __name__ == "__main__":
    main()
