"""Measure how each decoder holds up as a sender gets less consistent.

Run it with:
    python benchmark.py

It writes, into the results/ folder:
    benchmark.csv   every number
    summary.md      the headline table
    benchmark.png   the charts

Each test point decodes every phrase in synth.PHRASES several times with
different random seeds, then averages the character error rate (CER). Five
kinds of imperfection are swept one at a time, holding the others at a mildly
imperfect default, so each chart shows the effect of exactly one thing.

The decoders compared:
    Textbook                 NaiveDecoder, which is TOLD the sender's true
                             starting speed (a generous baseline) - except in the
                             "speed" sweep, where it's fixed at 10 WPM the way a
                             real device ships with one default setting.
    Adaptive (no setup)      AdaptiveDecoder starting from nothing.
    Adaptive + calibration   AdaptiveDecoder seeded from one PARIS sent by the
                             same synthetic person (with different random noise).
    Adaptive, no repair      Same as the line above but without the invalid-
                             character repair step, to show what repair adds.
"""

import csv
import statistics
from dataclasses import replace
from pathlib import Path

from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.calibration import CalibrationError, calibrate
from morse_aac.metrics import character_error_rate, word_accuracy
from morse_aac.naive import NaiveDecoder
from morse_aac.synth import PHRASES, SenderProfile, synthesize

SEEDS = range(5)
RESULTS = Path(__file__).parent / "results"

DEFAULT = SenderProfile(unit_ms=120, press_jitter=0.15, gap_jitter=0.15)

SWEEPS = {
    "jitter": ("Timing jitter (typical % off)", [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
               lambda s, v: replace(s, press_jitter=v, gap_jitter=v)),
    "drift": ("Slowdown by end of message (fatigue)", [0.0, 0.25, 0.5, 1.0, 1.5, 2.0],
              lambda s, v: replace(s, drift=v)),
    "dash_ratio": ("Dash length, in dots (textbook = 3)", [1.8, 2.0, 2.5, 3.0, 3.5, 4.0],
                   lambda s, v: replace(s, dash_ratio=v)),
    "gap_scale": ("Gaps stretched (slow release)", [1.0, 1.25, 1.5, 2.0, 2.5, 3.0],
                  lambda s, v: replace(s, gap_scale=v)),
    "speed": ("Sender speed, WPM (textbook set to 10)", [4, 6, 8, 10, 15, 20],
              lambda s, v: replace(s, unit_ms=1200 / v)),
}

DECODERS = ["Textbook", "Adaptive (no setup)", "Adaptive + calibration", "Adaptive, no repair"]


def run_decoders(sender: SenderProfile, sweep: str, phrase: str, seed: int) -> dict[str, str]:
    events = synthesize(phrase, sender, seed=seed)
    naive_unit = 120.0 if sweep == "speed" else sender.unit_ms

    try:
        timing = calibrate(synthesize("PARIS", sender, seed=10_000 + seed))
    except CalibrationError:
        timing = None  # only possible if generation went badly wrong

    return {
        "Textbook": NaiveDecoder(naive_unit).decode(events),
        "Adaptive (no setup)": AdaptiveDecoder().decode(events),
        "Adaptive + calibration": AdaptiveDecoder(timing).decode(events),
        "Adaptive, no repair": AdaptiveDecoder(timing, repair=False).decode(events),
    }


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    rows = []
    for sweep, (_label, values, apply) in SWEEPS.items():
        for value in values:
            sender = apply(DEFAULT, value)
            cer = {d: [] for d in DECODERS}
            words = {d: [] for d in DECODERS}
            for phrase in PHRASES:
                for seed in SEEDS:
                    outputs = run_decoders(sender, sweep, phrase, seed)
                    for d, text in outputs.items():
                        cer[d].append(character_error_rate(phrase, text))
                        words[d].append(word_accuracy(phrase, text))
            for d in DECODERS:
                rows.append({
                    "sweep": sweep, "value": value, "decoder": d,
                    "cer": statistics.mean(cer[d]),
                    "word_accuracy": statistics.mean(words[d]),
                    "trials": len(cer[d]),
                })
            summary = "  ".join(f"{d}: {statistics.mean(cer[d]):6.1%}" for d in DECODERS[:3])
            print(f"{sweep:>10} = {value:<5}  {summary}")

    with open(RESULTS / "benchmark.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    write_summary(rows)
    try:
        from plot_results import plot
        plot(rows, SWEEPS, RESULTS / "benchmark.png")
        print(f"\nChart saved to {RESULTS / 'benchmark.png'}")
    except ImportError:
        print("\n(matplotlib not installed, so no chart. `pip install matplotlib` to get one.)")
    print(f"Numbers saved to {RESULTS / 'benchmark.csv'} and {RESULTS / 'summary.md'}")


def write_summary(rows: list[dict]) -> None:
    def cer(sweep, value, decoder):
        for r in rows:
            if r["sweep"] == sweep and r["value"] == value and r["decoder"] == decoder:
                return r["cer"]

    lines = [
        "# Benchmark summary",
        "",
        f"Character error rate (lower is better), averaged over {len(PHRASES)} phrases "
        f"x {len(SEEDS)} random seeds per cell.",
        "",
        "| Condition | " + " | ".join(DECODERS) + " |",
        "| --- | " + " | ".join("---" for _ in DECODERS) + " |",
    ]
    for sweep, (label, values, _) in SWEEPS.items():
        for value in values:
            cells = " | ".join(f"{cer(sweep, value, d):.1%}" for d in DECODERS)
            lines.append(f"| {label}: {value} | {cells} |")
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
