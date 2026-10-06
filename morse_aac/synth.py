"""Synthetic senders: turn text into Events the way a real, imperfect person might.

There's no public dataset of Morse sent by people with unsteady motor control,
so this generates it, with each kind of imperfection as its own dial. That makes
it possible to say exactly *which* kind of inconsistency a decoder handles, and
to plot accuracy as each one gets worse.

The dials (all default to "perfect textbook sender"):

    unit_ms      how fast they send (1 unit = one dot). 120ms is 10 WPM.
    press_jitter random variation in each press, as the standard deviation of
                 the log of the duration. 0.2 means a typical press is about
                 20% off its intended length.
    gap_jitter   the same for silences. Letting go of a switch on time is often
                 harder than pressing it, so this is set separately.
    drift        gradual slowing across the message (fatigue). 1.0 means they
                 end the message at half their starting speed (2x the unit).
    dash_ratio   how long their dashes are, in dots. The textbook says 3.
    gap_scale    stretches every silence (someone slow to release the switch).

Those senders have a rhythm, however rough. The second half of this file is a
different kind of sender: a Learner, who doesn't. They are still looking each
letter up, so their gaps come from thinking, not from rhythm, and they watch
the screen and wait for each letter to appear before starting the next. That
makes what they send depend on what the decoder shows them, so a Learner
types into a live session instead of producing a list of Events up front.
"""

import math
import random
from dataclasses import dataclass

from .events import Event
from .table import SPACE_CODE, encode

# A long hold, in the simulated sender's plans, and how many of their own
# dashes long they make it.
LONG_HOLD = "_"
LONG_HOLD_DASHES_SENT = 4.0


@dataclass
class SenderProfile:
    unit_ms: float = 120.0
    press_jitter: float = 0.0
    gap_jitter: float = 0.0
    drift: float = 0.0
    dash_ratio: float = 3.0
    gap_scale: float = 1.0


def synthesize(text: str, sender: SenderProfile, seed: int | None = None,
               spaces: str = "timing") -> list[Event]:
    """spaces='timing' separates words with a long pause (7 units, as normal
    Morse does); spaces='code' sends the space code ..-- as its own character
    instead, as in assistive typing; spaces='hold' sends one long hold
    (LONG_HOLD_DASHES_SENT of their dashes long) as the space."""
    rng = random.Random(seed)
    words = encode(text)
    if spaces in ("code", "hold"):
        # Send the space as a character between words, all in one "word".
        joined: list[str] = []
        for wi, word in enumerate(words):
            if wi:
                joined.append(SPACE_CODE if spaces == "code" else LONG_HOLD)
            joined.extend(word)
        words = [joined] if joined else []

    # Nominal (intended) sequence first, in units, so drift can be applied
    # according to how far through the message each element is.
    plan: list[tuple[bool, float]] = []
    for wi, word in enumerate(words):
        if wi:
            plan.append((False, 7 * sender.gap_scale))
        for ci, pattern in enumerate(word):
            if ci:
                plan.append((False, 3 * sender.gap_scale))
            for si, symbol in enumerate(pattern):
                if si:
                    plan.append((False, 1 * sender.gap_scale))
                plan.append((True, {".": 1.0, "-": sender.dash_ratio,
                                    LONG_HOLD: LONG_HOLD_DASHES_SENT * sender.dash_ratio}[symbol]))

    total_units = sum(units for _, units in plan) or 1.0
    events: list[Event] = []
    elapsed_units = 0.0
    for is_on, units in plan:
        progress = elapsed_units / total_units
        speed = 1.0 + sender.drift * progress   # unit length multiplier
        jitter = sender.press_jitter if is_on else sender.gap_jitter
        noise = math.exp(rng.gauss(0.0, jitter)) if jitter > 0 else 1.0
        events.append(Event(is_on, units * sender.unit_ms * speed * noise))
        elapsed_units += units
    return events


@dataclass
class Learner:
    dot_ms: float = 130.0
    dash_ms: float = 450.0
    inside_ms: float = 350.0      # between presses inside a letter
    think_ms: float = 600.0       # after a letter shows, before starting the next
    press_wobble: float = 0.25    # as press_jitter above
    gap_wobble: float = 0.35      # the same for inside_ms and think_ms


def type_live(session, text: str, learner: Learner, seed: int | None = None,
              tick_ms: float = 20.0, patience_ms: float = 4000.0) -> float:
    """Type `text` into a LiveSession the way a Learner would. Returns the
    time, in ms, at which they finished.

    Between letters they wait until the letter in progress has left the
    "letter so far" line, and between words until the space has appeared;
    then they think, and carry on. If the screen hasn't done what they were
    waiting for after `patience_ms`, they carry on anyway.
    """
    rng = random.Random(seed)
    decoder = session.decoder
    t = 0.0

    def wait(ms: float) -> None:
        nonlocal t
        end = t + ms
        while t < end:
            t = min(end, t + tick_ms)
            session.tick(t)

    def wait_until(shown) -> None:
        nonlocal t
        give_up = t + patience_ms
        while not shown() and t < give_up:
            t += tick_ms
            session.tick(t)

    def about(ms: float, wobble: float) -> float:
        return ms * math.exp(rng.gauss(0.0, wobble))

    def letter_shown() -> bool:
        return getattr(decoder, "initialized", True) and not decoder.pending_pattern

    for wi, word in enumerate(encode(text)):
        if wi:
            wait_until(letter_shown)
            wait_until(lambda: decoder.text.endswith(" "))
            wait(about(learner.think_ms, learner.gap_wobble))
        for ci, pattern in enumerate(word):
            if ci:
                wait_until(letter_shown)
                wait(about(learner.think_ms, learner.gap_wobble))
            for si, symbol in enumerate(pattern):
                if si:
                    wait(about(learner.inside_ms, learner.gap_wobble))
                session.press_down(t)
                t += about(learner.dot_ms if symbol == "." else learner.dash_ms,
                           learner.press_wobble)
                session.press_up(t)
    wait_until(letter_shown)
    return t


# Short phrases of the kind someone might actually need to say with an AAC
# device, plus a couple of pangrams so every letter gets exercised.
PHRASES = [
    "I NEED WATER",
    "PLEASE CALL MY NURSE",
    "I AM IN PAIN",
    "TURN ON THE LIGHT",
    "CAN YOU OPEN THE WINDOW",
    "I WANT TO GO OUTSIDE",
    "THANK YOU",
    "HOW ARE YOU TODAY",
    "I AM COLD",
    "PLEASE WAIT",
    "YES",
    "NO NOT NOW",
    "MOVE MY PILLOW",
    "CALL MY MOM",
    "I LOVE YOU",
    "WHAT TIME IS IT",
    "I AM TIRED",
    "HELP ME SIT UP",
    "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG",
    "PACK MY BOX WITH FIVE DOZEN LIQUOR JUGS",
]
