"""What happens when the person typing is still learning the code?

    python learner_report.py

Writes results/learner.md. Takes a few minutes.

benchmark.py measures senders who have a rhythm, however rough. The first
person to really use the app didn't: they calibrated with quick taps, then
typed slowly, working out each press. Letters were finished before they could
get to the second press, and most of what came out was E's and T's.

This script reproduces that with a simulated learner (synth.Learner, who
watches the screen and waits for each letter to appear) typing into the same
live session the app uses, and measures the two things added because of it:

  1. the letter pause: one second of silence finishes a letter, nothing shorter does;
  2. the second opinion: when the model's dot and dash plainly contradict the
     recent presses, it is restarted from them (see adaptive.py).

Every number is from simulated typing. See morse_aac/synth.py for what the
simulated learner does and doesn't do.
"""

import statistics
from pathlib import Path

from morse_aac.adaptive import AdaptiveDecoder, standard_timing
from morse_aac.metrics import character_error_rate
from morse_aac.session import LiveSession
from morse_aac.synth import PHRASES, Learner, type_live

SEEDS = (0, 1)
LETTER_PAUSE_MS = 1000.0
RESULTS = Path(__file__).parent / "results"

LEARNER = Learner()
# Where each person's timing would put a calibration done the way they really
# type. Nothing is shown during calibration, so the gap between letters is
# their own idea of "a little pause".
PEOPLE = {
    "A learner (dot 130 ms, dash 450 ms)": Learner(),
    "Quick presses (80 ms, 260 ms)": Learner(dot_ms=80, dash_ms=260, inside_ms=200),
    "Slow presses (250 ms, 800 ms)": Learner(dot_ms=250, dash_ms=800, inside_ms=400),
}


def own_timing(person: Learner) -> dict[str, float]:
    return {"dot": person.dot_ms, "dash": person.dash_ms, "intra": person.inside_ms,
            "char": 1200.0, "word": 2800.0}


def letters_wrong(person: Learner, timing: dict[str, float] | None, **settings) -> float:
    """Average share of letters wrong, typing every phrase with every seed."""
    errors = []
    for i, phrase in enumerate(PHRASES):
        for seed in SEEDS:
            decoder = AdaptiveDecoder(timing, spaces="pause", **settings)
            session = LiveSession(decoder)
            type_live(session, phrase, person, seed=1000 * seed + i, tick_ms=25.0)
            decoder.finish()
            errors.append(character_error_rate(phrase, decoder.text))
    return statistics.mean(errors)


def main() -> None:
    lines = [
        "# Typing by someone still learning the code",
        "",
        f"Letters wrong, averaged over {len(PHRASES)} phrases x {len(SEEDS)} seeds, typed "
        "into the live session by a simulated learner who waits for each letter to appear. "
        "Above 100% means more wrong letters came out than the message has letters.",
        "",
        "## The letter pause",
        "",
        "The learner: dots about 130 ms, dashes about 450 ms, about 350 ms between presses "
        "inside a letter (varying a lot), and about 0.6 s of thinking after each letter shows.",
        "",
        "| The decoder starts from | Letters end by learned timing | "
        "Letters end after a 1 s pause |",
        "| --- | --- | --- |",
    ]
    starts = {
        "A calibration typed the way they really type": own_timing(LEARNER),
        "A calibration tapped out quickly (20 WPM)": standard_timing(60),
        "Nothing (learns as it goes)": None,
    }
    for name, timing in starts.items():
        learned = letters_wrong(LEARNER, timing)
        paused = letters_wrong(LEARNER, timing, letter_pause_ms=LETTER_PAUSE_MS)
        lines.append(f"| {name} | {learned:.0%} | {paused:.0%} |")
        print(f"{name:<48} learned {learned:6.1%}   1 s pause {paused:6.1%}")

    lines += [
        "",
        "## The second opinion",
        "",
        "Three people, each starting from a calibration that doesn't match how they type "
        "(the unit is the calibrated dot length). Letter pause 1 s throughout. Every phrase "
        "starts again from the mismatched calibration, so this is the first sentence after "
        "a bad calibration, over and over: the worst moment. In the app the corrected timing "
        "carries on to the next sentence and is saved.",
        "",
        "| Person | Calibrated at | Without second opinion | With |",
        "| --- | --- | --- | --- |",
    ]
    for name, person in PEOPLE.items():
        for unit in (40, 60, 120, 240):
            timing = standard_timing(unit)
            without = letters_wrong(person, timing, letter_pause_ms=LETTER_PAUSE_MS,
                                    second_opinion=False)
            with_it = letters_wrong(person, timing, letter_pause_ms=LETTER_PAUSE_MS)
            lines.append(f"| {name} | {unit} ms | {without:.0%} | {with_it:.0%} |")
            print(f"{name:<38} cal {unit:>3} ms   without {without:6.1%}   with {with_it:6.1%}")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "learner.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
