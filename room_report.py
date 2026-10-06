"""How well does the sound decoder cope with a room? (Simulated: see
morse_aac/room.py for what that does and doesn't include.)

    python room_report.py

Writes results/room.md. Takes about half a minute.

For each situation, 20 phrases x 2 random seeds are sent by a simulated person
with 15% timing wobble at 10 words per minute, played into the simulated room,
and decoded with no calibration. "No audio" is the same sender decoded
straight from their timing, which is the best any hearing could do.

The second half does the same for taps on a desk (morse_aac/taps.py): the
phrases tapped out by a simulated person, one tap for a dot and two quick taps
for a dash, as made-up knocks in the same simulated room.
"""

import statistics
from pathlib import Path

import numpy as np

from morse_aac.adaptive import AdaptiveDecoder
from morse_aac.audio import events_from_audio, synthesize_audio
from morse_aac.metrics import character_error_rate
from morse_aac.room import SCENARIOS, add_room, room_noise
from morse_aac.synth import PHRASES, SenderProfile, synthesize
from morse_aac.taps import (
    KnockDetector,
    TapDecoder,
    TapListener,
    TapReader,
    knock_audio,
    tap_times,
)

RATE = 8000
PITCH = 700.0
SEEDS = (0, 1)
SENDER = SenderProfile(unit_ms=120, press_jitter=0.15, gap_jitter=0.15)
NOISE_MINUTES = 10
RESULTS = Path(__file__).parent / "results"


def decode(audio: np.ndarray) -> str:
    return AdaptiveDecoder().decode(events_from_audio(audio, RATE, PITCH)[0])


def decode_taps(audio: np.ndarray) -> str:
    decoder = TapDecoder()
    listener = TapListener(RATE, decoder)
    for start in range(0, len(audio), 400):   # in chunks, as a microphone delivers it
        listener.feed(audio[start:start + 400])
    listener.finish()
    return decoder.text


def read_taps(times_ms: list[float]) -> str:
    decoder = TapDecoder()
    reader = TapReader(decoder)
    for t in times_ms:
        reader.knock(t)
    reader.finish()
    return decoder.text


# For taps the room's "clicks" matter most: they are what a knock is. Their
# loudness follows the room's `level`, so these differ in how loud the other
# sharp sounds are next to the taps (a tap peaks at about 0.3).
TAP_SCENARIOS = {
    "Quiet room": SCENARIOS["quiet room"],
    "Loud talking, nothing else": dict(SCENARIOS["talking louder than beeps"], clicks=False),
    "Typing nearby, much quieter than the taps": SCENARIOS["quiet room, typing"],
    "Other sharp sounds half as loud as the taps": SCENARIOS["fan and quiet talking"],
}


def taps_section(lines: list[str]) -> None:
    lines += [
        "",
        "# Taps on a desk, in the same room",
        "",
        f"Letters wrong, averaged over {len(PHRASES)} phrases x {len(SEEDS)} seeds. One tap "
        "is a dot, two quick taps a dash; the taps' timing wobbles by 15%.",
        "",
        "| Situation | Letters wrong | Messages exactly right |",
        "| --- | --- | --- |",
    ]

    def add(name: str, outputs: list[tuple[str, str]]) -> None:
        wrong = statistics.mean(character_error_rate(p, out) for p, out in outputs)
        exact = sum(p == out for p, out in outputs)
        lines.append(f"| {name} | {wrong:.1%} | {exact} of {len(outputs)} |")
        print(f"taps: {name:<44} {wrong:6.1%}   exact {exact}/{len(outputs)}")

    def tapped(i: int, seed: int) -> tuple[np.random.Generator, list[float]]:
        rng = np.random.default_rng(1000 * seed + i)
        return rng, tap_times(PHRASES[i], rng)

    add("No audio (the taps' timing itself)",
        [(PHRASES[i], read_taps(tapped(i, s)[1])) for i in range(len(PHRASES)) for s in SEEDS])
    for name, settings in TAP_SCENARIOS.items():
        outputs = []
        for i, phrase in enumerate(PHRASES):
            for seed in SEEDS:
                rng, times = tapped(i, seed)
                audio = knock_audio(times, RATE, rng)
                audio = audio + room_noise(len(audio), RATE, rng, **settings)
                outputs.append((phrase, decode_taps(audio)))
        add(name, outputs)

    lines.append("")
    for name in ("Loud talking, nothing else", "Typing nearby, much quieter than the taps"):
        knocks: list[float] = []
        detector = KnockDetector(RATE, knocks.append)
        for m in range(NOISE_MINUTES):
            detector.feed(room_noise(60 * RATE, RATE, np.random.default_rng(900 + m),
                                     **TAP_SCENARIOS[name]))
        lines.append(f"Noise only ({name.lower()}; no taps), {NOISE_MINUTES} minutes: "
                     f"{len(knocks)} taps heard.")
        print(f"taps, noise only, {name}: {len(knocks)} in {NOISE_MINUTES} minutes")


def main() -> None:
    lines = [
        "# Sound decoding in a simulated room",
        "",
        f"Letters wrong, averaged over {len(PHRASES)} phrases x {len(SEEDS)} seeds, "
        "10 WPM sender with 15% timing wobble, no calibration.",
        "",
        "| Situation | Letters wrong | Messages exactly right |",
        "| --- | --- | --- |",
    ]

    def add(name: str, outputs: list[tuple[str, str]]) -> None:
        wrong = statistics.mean(character_error_rate(p, out) for p, out in outputs)
        exact = sum(p == out for p, out in outputs)
        lines.append(f"| {name} | {wrong:.1%} | {exact} of {len(outputs)} |")
        print(f"{name:<32} {wrong:6.1%}   exact {exact}/{len(outputs)}")

    add("No audio (the sender's timing itself)",
        [(p, AdaptiveDecoder().decode(synthesize(p, SENDER, seed=s)))
         for p in PHRASES for s in SEEDS])

    for name, settings in SCENARIOS.items():
        outputs = []
        for i, phrase in enumerate(PHRASES):
            for seed in SEEDS:
                clean = synthesize_audio(synthesize(phrase, SENDER, seed=seed), RATE, PITCH)
                rng = np.random.default_rng(1000 * seed + i)
                outputs.append((phrase, decode(add_room(clean, RATE, rng, **settings))))
        add(name.capitalize(), outputs)

    loud = SCENARIOS["talking louder than beeps"]
    letters = sum(
        len(decode(room_noise(60 * RATE, RATE, np.random.default_rng(900 + m), **loud))
            .replace(" ", ""))
        for m in range(NOISE_MINUTES))
    lines += ["", f"Noise only (loud talking, clicks, rumble; no beeps), {NOISE_MINUTES} minutes: "
                  f"{letters} letters decoded."]
    print(f"noise only, {NOISE_MINUTES} minutes: {letters} letters")

    taps_section(lines)

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "room.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
