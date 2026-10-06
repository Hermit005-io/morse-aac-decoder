"""Accuracy measures, the same ones speech and handwriting recognition use."""


def levenshtein(a: str, b: str) -> int:
    """Fewest single-character insertions, deletions or substitutions that turn
    `a` into `b` (classic dynamic programming, one row at a time)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,               # delete
                current[j - 1] + 1,            # insert
                previous[j - 1] + (ca != cb),  # substitute (free if equal)
            ))
        previous = current
    return previous[-1]


def character_error_rate(reference: str, decoded: str) -> float:
    """Edit distance as a fraction of the reference length. 0.0 is perfect.
    Can go above 1.0 if the decoder produces lots of extra characters."""
    if not reference:
        return 0.0 if not decoded else 1.0
    return levenshtein(reference, decoded) / len(reference)


def word_accuracy(reference: str, decoded: str) -> float:
    """Fraction of reference words that came out exactly right, in position."""
    ref_words = reference.split()
    if not ref_words:
        return 1.0
    out_words = decoded.split()
    correct = sum(1 for i, w in enumerate(ref_words) if i < len(out_words) and out_words[i] == w)
    return correct / len(ref_words)
