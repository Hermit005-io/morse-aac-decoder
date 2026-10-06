"""Draws the benchmark chart. Called by benchmark.py; needs matplotlib."""

import matplotlib

matplotlib.use("Agg")  # draw to a file, no window needed
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

SERIES = [
    ("Textbook", "#2a78d6", "o"),
    ("Adaptive (no setup)", "#eb6834", "s"),
    ("Adaptive + calibration", "#1baf7a", "^"),
]


def _format_value(sweep: str, value: float) -> str:
    if sweep == "jitter":
        return f"{value:.0%}"
    if sweep == "drift":
        return f"{1 + value:g}x"
    if sweep == "gap_scale":
        return f"{value:g}x"
    return f"{value:g}"


def plot(rows: list[dict], sweeps: dict, path) -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_SECONDARY,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "text.color": INK,
    })
    fig, axes = plt.subplots(1, len(sweeps), figsize=(3.1 * len(sweeps), 3.6),
                             sharey=True, facecolor=SURFACE)

    for ax, (sweep, (label, values, _)) in zip(axes, sweeps.items(), strict=True):
        ax.set_facecolor(SURFACE)
        positions = list(range(len(values)))
        for name, color, marker in SERIES:
            cer = [next(r["cer"] for r in rows
                        if r["sweep"] == sweep and r["value"] == v and r["decoder"] == name)
                   for v in values]
            ax.plot(positions, [min(c, 1.0) * 100 for c in cer], color=color, lw=2,
                    marker=marker, markersize=6.5, markeredgecolor=SURFACE,
                    markeredgewidth=1.5, solid_capstyle="round", label=name, zorder=3)
        ax.set_xticks(positions, [_format_value(sweep, v) for v in values])
        ax.set_title(label, fontsize=10, color=INK, loc="left", wrap=True)
        ax.set_ylim(0, 105)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(length=0)

    axes[0].set_ylabel("Characters wrong (%)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", ncol=3, frameon=False,
               bbox_to_anchor=(0.01, 1.02), fontsize=10)
    fig.text(0.01, -0.04,
             "Lower is better. Character error rate, 20 phrases x 5 seeds per point; "
             "values above 100% (more errors than characters) are shown at 100%. "
             "Textbook decoder is given the sender's true speed, except in the speed panel.",
             fontsize=8.5, color=INK_SECONDARY)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
