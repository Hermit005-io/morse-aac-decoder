"""The shared input format for every decoder.

Whatever the source (a key, a mouse button, a tone on a microphone), input is
reduced to one thing: a list of Events. Each Event says whether the signal was
ON (switch pressed / tone playing) or OFF (released / silent), and for how long.

    [Event(True, 110.0), Event(False, 95.0), Event(True, 340.0), ...]
      press 110ms         gap 95ms            press 340ms

ON and OFF always alternate, starting with ON.
"""

from typing import NamedTuple


class Event(NamedTuple):
    is_on: bool
    duration_ms: float


def events_from_timestamps(presses: list[tuple[float, float]]) -> list[Event]:
    """Build Events from (press_time_ms, release_time_ms) pairs, in order."""
    events: list[Event] = []
    last_release = None
    for down, up in presses:
        if last_release is not None:
            events.append(Event(False, down - last_release))
        events.append(Event(True, up - down))
        last_release = up
    return events
