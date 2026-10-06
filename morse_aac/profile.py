"""Saving and loading a person's timing and settings between sessions, as two
small JSON files in their home folder:

    ~/.morse_aac/profile.json    learned timing ("Forget my timing" deletes it)
    ~/.morse_aac/settings.json   choices like how word spaces are typed
"""

import json
import math
from pathlib import Path

DEFAULT_PATH = Path.home() / ".morse_aac" / "profile.json"
SETTINGS_NAME = "settings.json"


def save_profile(timing: dict[str, float], noise: dict[str, float] | None = None,
                 path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"timing_ms": timing}
    if noise:
        data["noise"] = noise
    path.write_text(json.dumps(data, indent=2))


Profile = tuple[dict[str, float], dict[str, float] | None]


def load_profile(path: Path = DEFAULT_PATH) -> Profile | None:
    """Returns (timing, noise), or None if there's no usable saved profile."""
    try:
        data = json.loads(path.read_text())
        timing = {k: float(v) for k, v in data["timing_ms"].items()}
        if not {"dot", "dash", "intra", "char", "word"} <= timing.keys():
            return None
        if not all(0 < v < math.inf for v in timing.values()):
            return None   # a length of zero or less can't be anyone's timing
        noise = {k: float(v) for k, v in (data.get("noise") or {}).items()}
        usable = noise.keys() >= {"press", "gap"} and all(0 < v < math.inf for v in noise.values())
        return timing, (noise if usable else None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def delete_profile(path: Path = DEFAULT_PATH) -> None:
    path.unlink(missing_ok=True)


def load_settings(profile_path: Path = DEFAULT_PATH) -> dict:
    """Settings live next to the profile. Missing or unreadable -> {}."""
    try:
        data = json.loads((profile_path.parent / SETTINGS_NAME).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict, profile_path: Path = DEFAULT_PATH) -> None:
    path = profile_path.parent / SETTINGS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2))
