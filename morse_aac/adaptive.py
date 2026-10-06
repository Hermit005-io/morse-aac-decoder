"""The adaptive decoder: the core of this project.

Instead of assuming textbook timing, it keeps a model of *this person's* timing
and keeps updating it as they send.

THE MODEL
---------
Every press or gap belongs to one of five kinds: dot, dash, intra (gap inside a
character), char (gap between characters), word (gap between words). The
typical length of each kind is modelled as

    typical length = unit  x  textbook units  x  personal stretch

    unit              one number shared by everything: how fast they're going
                      right now. When someone tires and slows down, this moves.
    textbook units    1, 3, 1, 3, 7 for dot, dash, intra, char, word.
    personal stretch  one number per kind: how this person differs from the
                      textbook. Dashes only 2x a dot? The dash stretch is 2/3.
                      Slow to let go of the switch? The gap stretches are > 1.

Everything is done in logs, because timing errors are proportional: 50ms off
is a big miss for a 100ms dot and a small one for a 400ms dash. In log terms,
each kind's actual lengths scatter around its typical length like a bell curve,
and the width of that bell curve is the person's "noise".

READING A PRESS: PROBABILITIES, NOT A HARD CUT-OFF
--------------------------------------------------
For each press, the decoder asks: given the bell curves for dot and dash, and
how common each is, how likely is it that this press was a dot? (Bayes' rule.)
It picks the likelier one, and the probability doubles as its confidence.

Why not just cut halfway between dot and dash? Because when timing is shaky the
bell curves overlap, and which way to lean near the middle depends on how wide
they are and how common each kind is (dots are more common than dashes; gaps
inside characters are much more common than gaps between words).

LEARNING: A KALMAN FILTER, FED WITH PROBABILITIES
-------------------------------------------------
Each press/gap then corrects the model. The hard question is *how much*:

  * Correct too little, and you can't follow someone who is slowing down.
  * Correct too much, and random wobble yanks the model around, which causes
    misreads, which pull the model further off (a feedback loop).

A Kalman filter answers this by tracking how *uncertain* each part of the model
is, and how *noisy* the person is:

    correction  =  error  x  uncertainty / (uncertainty + noise)

  * Uncertainty grows a little with every press (the person might be drifting)
    and shrinks every time a measurement is used.
  * Noise is measured on the fly from the size of recent errors. A steady
    sender gets closely followed; a shaky one gets a slow, stable model.

And a press that was 70% likely a dot updates the dot estimate with 70% of the
weight and the dash estimate with 30%. Learning only from the single best guess
would be biased: long-ish dots that got read as dashes would never count toward
the dot estimate, so it would creep shorter and shorter. (This is online
Expectation-Maximisation, the standard fix for exactly that bias.)

Guard rails: no single measurement can count as more than `max_step` times off,
and neighbouring kinds can't collapse into each other.

A SECOND OPINION
----------------
All of that assumes the model starts roughly right. Sometimes it doesn't:
someone calibrates with quick, careful taps and then types at half that speed.
Small corrections do get there, but only after a dozen or more presses, and
the model can read every press as a dash in the meantime.

So the decoder also keeps the last few press lengths and, with no model at
all, asks whether they fall into two clearly separate groups (see
`_second_opinion`). If they do, and the model disagrees about which group a
good share of them belong to, the model is the one that's wrong, and its dot
and dash lengths are restarted from the two groups.

GETTING STARTED
---------------
With a saved profile or a calibration (see calibration.py), the model starts
from those numbers. With neither, the decoder waits for the first few presses,
finds the natural short/long split in them (see `_bootstrap`), and starts from
there with high uncertainty so it settles quickly.
"""

import math
from collections import deque

from .decoder_base import (
    DEFAULT_SPACE_PAUSE_MS,
    LONG_HOLD_DASHES,
    LONG_HOLD_MIN_MS,
    BaseDecoder,
)
from .events import Event

KINDS = ("dot", "dash", "intra", "char", "word")
TEXTBOOK_UNITS = {"dot": 1, "dash": 3, "intra": 1, "char": 3, "word": 7}
PRESS_KINDS = ("dot", "dash")
GAP_KINDS = ("intra", "char", "word")
GROUP = {"dot": "press", "dash": "press", "intra": "gap", "char": "gap", "word": "gap"}
SYMBOL = {"dot": ".", "dash": "-"}

# Roughly how often each kind occurs in English Morse; learned as it goes.
STARTING_FREQUENCY = {"dot": 0.55, "dash": 0.45, "intra": 0.55, "char": 0.33, "word": 0.12}

# Minimum ratio kept between neighbouring kinds.
MIN_RATIO = {("dot", "dash"): 1.6, ("intra", "char"): 1.6, ("char", "word"): 1.4}

# The second opinion: how many recent presses it looks at, how few are enough,
# and the share of them the model must disagree on before it is overruled.
RECENT_PRESSES = 12
SECOND_OPINION_MIN_PRESSES = 8
SECOND_OPINION_DISAGREEMENT = 1 / 3


def standard_timing(unit_ms: float) -> dict[str, float]:
    """Textbook timing for a given unit length, in ms."""
    return {k: unit_ms * n for k, n in TEXTBOOK_UNITS.items()}


class AdaptiveDecoder(BaseDecoder):
    def __init__(
        self,
        timing: dict[str, float] | None = None,
        noise: dict[str, float] | None = None,
        speed_drift: float = 0.002,
        stretch_drift: float = 0.0005,
        initial_uncertainty: float = 0.005,
        max_step: float = 2.0,
        min_share: float = 0.1,
        repair: bool = True,
        spaces: str = "timing",
        space_pause_ms: float = DEFAULT_SPACE_PAUSE_MS,
        letter_pause_ms: float = 0.0,
        second_opinion: bool = True,
        bootstrap_presses: int = 6,
        prior_unit_ms: float = 120.0,
    ):
        """
        timing:              starting typical lengths in ms, keyed by kind (from a
                             saved profile or a calibration). If None, the decoder
                             bootstraps itself from the first presses.
        noise:               starting noise {'press': v, 'gap': v}, as variances of
                             log-duration (from a saved profile).
        speed_drift:         how much overall speed is expected to wander per
                             press/gap. Bigger follows fatigue faster but reacts
                             more to random wobble.
        stretch_drift:       the same for the personal stretches, which should
                             change much more slowly than speed.
        initial_uncertainty: how unsure the starting timing is.
        max_step:            cap on how far off one measurement may count as.
        spaces:              how word spaces are typed: 'timing', 'code' or 'pause'
        space_pause_ms:      (with the length of that pause); see decoder_base.py.
        letter_pause_ms:     a silence this long ends a letter, and nothing shorter
                             does; see decoder_base.py. 0 leaves it to the model.
        min_share:           a measurement only updates a kind it's at least this
                             likely to belong to. Without this, a run of nothing
                             but dots slowly drags the dash estimate down toward
                             them, since each dot is 'a little bit dash'.
        second_opinion:      whether to check the model against the recent presses
                             themselves (see `_second_opinion`). Only turned off
                             to measure what it adds.
        bootstrap_presses:   presses to collect before starting, if no timing given.
        prior_unit_ms:       only used when bootstrapping from input so ambiguous
                             that no short/long split exists (e.g. all dots, "HIS").
                             120ms is 10 words per minute.
        """
        super().__init__(repair=repair, spaces=spaces, space_pause_ms=space_pause_ms,
                         letter_pause_ms=letter_pause_ms)
        self.speed_drift = speed_drift
        self.stretch_drift = stretch_drift
        self.initial_uncertainty = initial_uncertainty
        self.max_step = max_step
        self.min_share = min_share
        self.second_opinion = second_opinion
        self.bootstrap_presses = bootstrap_presses
        self.prior_unit_ms = prior_unit_ms

        self._speed = 0.0                              # log of the unit, in ms
        self._stretch = {k: 0.0 for k in KINDS}        # log of each personal stretch
        self._speed_var = 0.0                          # uncertainty of the speed
        self._stretch_var = {k: 0.0 for k in KINDS}    # uncertainty of each stretch
        self._noise = dict(noise) if noise else {"press": 0.03, "gap": 0.03}
        self._error_power = dict(self._noise)          # running mean of error^2
        self._frequency = dict(STARTING_FREQUENCY)
        self._initialized = False
        self._pending: list[Event] = []
        self._recent_presses: deque[float] = deque(maxlen=RECENT_PRESSES)
        self.restarts = 0   # times the second opinion overruled the model

        if timing is not None:
            self._start_from(timing, initial_uncertainty)

    # ---- public view of the model ------------------------------------------------------
    @property
    def initialized(self) -> bool:
        return self._initialized

    def timing(self) -> dict[str, float]:
        """Current typical length of each kind, in ms."""
        return {k: math.exp(self._log_typical(k)) for k in KINDS}

    def noise(self) -> dict[str, float]:
        """Current noise (variance of log-duration) for presses and for gaps."""
        return dict(self._noise)

    def estimated_wpm(self) -> float | None:
        """Speed in words per minute from the dot length (standard 1200 / unit ms)."""
        if not self._initialized:
            return None
        return 1200.0 / self.timing()["dot"]

    # ---- reading presses and gaps ----------------------------------------------------------
    def probabilities(self, duration_ms: float, kinds: tuple[str, ...]) -> dict[str, float]:
        """How likely this duration is to be each of `kinds` (sums to 1)."""
        y = math.log(max(duration_ms, 1.0))
        group = GROUP[kinds[0]]
        scores = {}
        for k in kinds:
            spread = self._noise[group] + self._speed_var + self._stretch_var[k]
            distance = y - self._log_typical(k)
            scores[k] = (math.log(self._frequency[k]) - 0.5 * math.log(spread)
                         - 0.5 * distance * distance / spread)
        top = max(scores.values())
        weights = {k: math.exp(s - top) for k, s in scores.items()}
        total = sum(weights.values())
        return {k: w / total for k, w in weights.items()}

    def classify_press(self, duration_ms: float) -> tuple[str, float]:
        kind, confidence = _best(self.probabilities(duration_ms, PRESS_KINDS))
        return SYMBOL[kind], confidence

    def classify_gap(self, duration_ms: float) -> tuple[str, float]:
        return _best(self.probabilities(duration_ms, GAP_KINDS))

    def char_gap_threshold_ms(self) -> float:
        return self._boundary_ms("intra", "char")

    def word_gap_threshold_ms(self) -> float:
        return self._boundary_ms("char", "word")

    def typical_char_gap_ms(self) -> float:
        return self.timing()["char"]

    def typical_dash_ms(self) -> float:
        return self.timing()["dash"]

    def _boundary_ms(self, shorter: str, longer: str) -> float:
        """The duration where `longer` becomes the likelier of the two (bisection)."""
        low, high = self._log_typical(shorter), self._log_typical(longer)
        for _ in range(40):
            mid = (low + high) / 2
            p = self.probabilities(math.exp(mid), (shorter, longer))
            if p[shorter] > p[longer]:
                low = mid
            else:
                high = mid
        return math.exp((low + high) / 2)

    # ---- learning -----------------------------------------------------------------------
    def learn_press(self, duration_ms: float, symbol: str) -> None:
        self._learn(duration_ms, PRESS_KINDS)

    def learn_gap(self, duration_ms: float, kind: str, overruled: bool = False) -> None:
        if overruled:
            self._correct(kind, duration_ms)
        else:
            self._learn(duration_ms, GAP_KINDS)

    def _correct(self, kind: str, duration_ms: float) -> None:
        """A gap the model read one way when the letter pause says it was
        `kind`. The model's idea of how long this person's `kind` gaps are is
        wrong, and it was surer than it had any right to be. So its
        uncertainty about them goes up to at least the size of the noise,
        which makes this one correction half the (capped) error.

        Only that one stretch is corrected. Someone who stops to work out
        the next press isn't sending more slowly, so the speed, and with it
        the dot and dash lengths, is left alone.
        """
        limit = math.log(self.max_step)
        y = math.log(max(duration_ms, 1.0))
        error = max(-limit, min(limit, y - self._log_typical(kind)))
        uncertainty = max(self._stretch_var[kind], self._noise["gap"])
        gain = uncertainty / (uncertainty + self._noise["gap"])
        self._stretch[kind] += gain * error
        self._stretch_var[kind] = uncertainty * (1 - gain)
        self._enforce_separation()

    def _learn(self, duration_ms: float, kinds: tuple[str, ...]) -> None:
        probs = self.probabilities(duration_ms, kinds)
        group = GROUP[kinds[0]]
        y = math.log(max(duration_ms, 1.0))
        limit = math.log(self.max_step)

        # Measure the person's noise: the expected squared error, minus the part
        # explained by the model's own uncertainty.
        expected_sq_error = 0.0
        explained = 0.0
        for k, p in probs.items():
            e = max(-limit, min(limit, y - self._log_typical(k)))
            expected_sq_error += p * e * e
            explained += p * (self._speed_var + self._stretch_var[k])
        self._error_power[group] = 0.95 * self._error_power[group] + 0.05 * expected_sq_error
        self._noise[group] = max(0.003, self._error_power[group] - explained)

        # Kalman step for each kind, weighted by how likely it is that this
        # measurement belongs to it. Low probability = treated as very noisy.
        for k, p in probs.items():
            if p < self.min_share:
                continue
            error = max(-limit, min(limit, y - self._log_typical(k)))
            noise = self._noise[group] / p
            total = self._speed_var + self._stretch_var[k] + noise
            speed_gain = self._speed_var / total
            stretch_gain = self._stretch_var[k] / total
            self._speed += speed_gain * error
            self._stretch[k] += stretch_gain * error
            self._speed_var *= 1 - speed_gain
            self._stretch_var[k] *= 1 - stretch_gain

        # Keep track of how common each kind is (kept above a small floor, so a
        # kind that hasn't appeared for a while can still be recognised).
        for k in kinds:
            self._frequency[k] = max(0.98 * self._frequency[k] + 0.02 * probs[k], 0.05)
        total_frequency = sum(self._frequency[k] for k in kinds)
        for k in kinds:
            self._frequency[k] /= total_frequency

        # Time passes: the person may be drifting, so uncertainty grows again.
        self._speed_var += self.speed_drift
        for k in KINDS:
            self._stretch_var[k] += self.stretch_drift

        self._enforce_separation()

    def _second_opinion(self) -> None:
        """Check the model's dot and dash against the recent presses themselves.

        Deliberately cautious. It only speaks up when the recent presses form
        two groups with a real hole between them, each with at least three
        presses and neither of which splits in two again (three groups means
        dots, dashes and long holds, and no way to tell which two are which).
        And it only overrules the model when the model reads at least a third
        of those presses differently. A model that is working puts its
        boundary in the hole and agrees on every press, so nothing happens.

        A press the model reads as a long hold never counts as a
        disagreement: dots and holds also make two groups (someone typing
        E, space, E, space), and there the model is right.

        It can still be fooled. A run of presses of one kind sometimes
        scatters into two groups by chance, and a model that was fine gets
        restarted; the README has how often, and what it costs.
        """
        if not self.second_opinion or len(self._recent_presses) < SECOND_OPINION_MIN_PRESSES:
            return
        split = _clear_split(list(self._recent_presses))
        if split is None:
            return
        dot, dash, boundary = split
        readings = [(self._read_press(ms), "-" if ms > boundary else ".")
                    for ms in self._recent_presses]
        disagreements = sum(model != groups for model, groups in readings if model != "hold")
        if disagreements < SECOND_OPINION_DISAGREEMENT * len(self._recent_presses):
            return

        # Restart the dot and dash from the two groups. The gaps keep the
        # lengths the model had for them: nothing here says they were wrong.
        gaps = {k: self._log_typical(k) for k in GAP_KINDS}
        self._speed = math.log(dot)
        self._stretch["dot"] = 0.0
        self._stretch["dash"] = math.log(dash / dot / TEXTBOOK_UNITS["dash"])
        for k in GAP_KINDS:
            self._stretch[k] = gaps[k] - self._speed - math.log(TEXTBOOK_UNITS[k])
        # As unsure as after bootstrapping from a handful of presses, which is
        # what this is. The noise was measured against a wrong model; forget it.
        self._speed_var = max(self._speed_var, 0.08)
        for k in PRESS_KINDS:
            self._stretch_var[k] = max(self._stretch_var[k], 0.02)
            self._frequency[k] = STARTING_FREQUENCY[k]
        self._noise["press"] = self._error_power["press"] = 0.03
        self._enforce_separation()
        self.restarts += 1

    def _read_press(self, duration_ms: float) -> str:
        """How the model reads a press right now: '.', '-' or 'hold'."""
        if self.is_long_hold(duration_ms):
            return "hold"
        return self.classify_press(duration_ms)[0]

    def _log_typical(self, kind: str) -> float:
        return self._speed + math.log(TEXTBOOK_UNITS[kind]) + self._stretch[kind]

    def _enforce_separation(self) -> None:
        for (low, high), ratio in MIN_RATIO.items():
            gap = self._log_typical(high) - self._log_typical(low)
            needed = math.log(ratio)
            if gap < needed:
                push = (needed - gap) / 2
                self._stretch[low] -= push
                self._stretch[high] += push

    def _start_from(self, timing: dict[str, float], uncertainty: float) -> None:
        self._speed = math.log(timing["dot"])
        for k in KINDS:
            self._stretch[k] = math.log(timing[k]) - self._speed - math.log(TEXTBOOK_UNITS[k])
            self._stretch_var[k] = uncertainty
        self._speed_var = uncertainty
        self._initialized = True
        self._enforce_separation()

    # ---- bootstrapping (no profile, no calibration) --------------------------------------
    def press(self, duration_ms: float) -> None:
        if not self._initialized:
            self._pending.append(Event(True, duration_ms))
            if sum(e.is_on for e in self._pending) >= self.bootstrap_presses:
                self._bootstrap()
            return
        super().press(duration_ms)
        self._recent_presses.append(duration_ms)
        self._second_opinion()

    def gap(self, duration_ms: float) -> None:
        if not self._initialized:
            self._pending.append(Event(False, duration_ms))
            return
        super().gap(duration_ms)

    def end_char(self) -> None:
        if not self._initialized:
            return
        super().end_char()

    def flush(self) -> None:
        self.force_init()
        super().flush()

    def clear(self) -> None:
        super().clear()
        self._pending.clear()   # presses still waiting for a timing model

    def force_init(self) -> None:
        """Start decoding with whatever has been buffered so far."""
        if not self._initialized and any(e.is_on for e in self._pending):
            self._bootstrap()

    def _dot_and_dash(self, presses: list[float], gaps: list[float]) -> tuple[float, float]:
        """A first guess at dot and dash length from a handful of presses."""
        split = _two_clusters(presses)
        if split is not None:
            return split
        # Every press looks alike. Decide dots vs dashes by comparing them to
        # the short gaps (usually about one unit long), or to the prior speed
        # if there are no gaps yet.
        typical = _geomean(presses)
        short_gaps = sorted(gaps)[: max(1, len(gaps) // 2)]
        reference = _geomean(short_gaps) if gaps else self.prior_unit_ms
        if typical < math.sqrt(3) * reference:
            return typical, 3 * typical
        return typical / 3, typical

    def _bootstrap(self) -> None:
        presses = [e.duration_ms for e in self._pending if e.is_on]
        gaps = [e.duration_ms for e in self._pending if not e.is_on]

        dot, dash = self._dot_and_dash(presses, gaps)
        if self.uses_codes:
            # One or two of these presses may be long holds (spaces), which
            # would wreck the estimate if counted as dashes. Try setting the
            # longest two, then the longest one, aside: if what's left gives
            # an estimate under which every press set aside really is a long
            # hold, that's the reading to use.
            ordered = sorted(presses)
            for held in (2, 1):
                rest, aside = ordered[:-held], ordered[-held:]
                if len(rest) < 2:
                    continue
                d, dd = self._dot_and_dash(rest, gaps)
                if min(aside) > max(LONG_HOLD_DASHES * dd, LONG_HOLD_MIN_MS):
                    dot, dash = d, dd
                    break

        timing = standard_timing(dot)
        timing["dash"] = dash
        gap_split = _two_clusters(gaps)
        if gap_split is not None:
            low, high = gap_split
            timing["intra"] = low
            if high / low < 4.5:  # the long gaps are character breaks
                timing["char"] = high
                timing["word"] = max(7 * low, 2.2 * high)
            else:                 # the long gaps are word breaks
                timing["char"] = 3 * low
                timing["word"] = high

        # A guess from a handful of presses: start quite unsure, so the first
        # real measurements correct it quickly.
        self._start_from(timing, uncertainty=max(self.initial_uncertainty, 0.08))
        # The stretches came from very few presses, but they're anchored by
        # textbook ratios; it's mostly the speed that is unknown.
        for k in KINDS:
            self._stretch_var[k] = max(self.initial_uncertainty, 0.02)
        buffered, self._pending = self._pending, []
        self.feed(buffered)


def _best(probs: dict[str, float]) -> tuple[str, float]:
    """The likeliest kind, and a 0..1 confidence: how far ahead of the runner-up
    it is (0 = a tie, 1 = certain)."""
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    return ranked[0][0], ranked[0][1] - runner_up


def _geomean(values: list[float]) -> float:
    return math.exp(sum(math.log(max(v, 1.0)) for v in values) / len(values))


def _best_split(logs: list[float]) -> int:
    """Where to cut sorted values into a low group and a high group so that
    each is as tight as possible (1-D k-means with k=2, solved exactly by
    trying every cut). Returns the index of the first value in the high group.
    """
    best_cost, best_cut = math.inf, 1
    for cut in range(1, len(logs)):
        left, right = logs[:cut], logs[cut:]
        ml, mr = sum(left) / len(left), sum(right) / len(right)
        cost = sum((x - ml) ** 2 for x in left) + sum((x - mr) ** 2 for x in right)
        if cost < best_cost:
            best_cost, best_cut = cost, cut
    return best_cut


def _two_clusters(values: list[float], min_ratio: float = 1.8) -> tuple[float, float] | None:
    """Split durations into a short group and a long group.

    Returns the typical (geometric mean) short and long durations, or None if
    the values don't really form two groups.
    """
    if len(values) < 2:
        return None
    logs = sorted(math.log(max(v, 1.0)) for v in values)
    cut = _best_split(logs)
    ml, mr = sum(logs[:cut]) / cut, sum(logs[cut:]) / (len(logs) - cut)
    if mr - ml < math.log(min_ratio):
        return None
    return math.exp(ml), math.exp(mr)


def _clear_split(values: list[float], min_size: int = 3, min_ratio: float = 2.0,
                 min_hole: float = 1.25) -> tuple[float, float, float] | None:
    """Like _two_clusters, but only when the two groups are beyond doubt:

      * each has at least `min_size` values,
      * the long group is typically at least `min_ratio` times the short one,
      * the shortest long value is at least `min_hole` times the longest
        short one (there is a real hole between the groups), and
      * neither group splits into two groups itself.

    Returns (typical short, typical long, the middle of the hole), or None.
    """
    ordered = sorted(max(v, 1.0) for v in values)
    logs = [math.log(v) for v in ordered]
    cut = _best_split(logs)
    low, high = ordered[:cut], ordered[cut:]
    if min(len(low), len(high)) < min_size:
        return None
    short, long = _geomean(low), _geomean(high)
    if long < min_ratio * short or high[0] < min_hole * low[-1]:
        return None
    if _two_clusters(low) is not None or _two_clusters(high) is not None:
        return None
    return short, long, math.sqrt(low[-1] * high[0])
