"""Server-rendered SVG chart for the How it works race.

The chart is drawn in Python and sent as inline SVG, so no chart library and
no client-side code are needed, and nothing is loaded from a CDN. Both axes
are logarithmic: on log-log axes a running time of n^k is a straight line of
slope k, so the engine's near-linear growth and the naive approach's
quadratic growth show up directly as slopes of about 1 and 2.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from eod_close.evaluation.benchmark import BenchmarkRow

WIDTH = 640
HEIGHT = 340
MARGIN_LEFT = 72
# Room for the series labels at the end of each line, the longest being
# "Checking every pair".
MARGIN_RIGHT = 160
MARGIN_TOP = 20
MARGIN_BOTTOM = 52


@dataclass(frozen=True)
class Point:
    """One plotted point and its tooltip."""

    x: float
    y: float
    label: str


@dataclass(frozen=True)
class Series:
    """One line on the chart.

    Attributes:
        key: CSS class suffix, which selects the series colour.
        name: Legend and direct-label text.
        points: The plotted points, left to right.
        slope: The fitted log-log slope, the empirical growth exponent.
    """

    key: str
    name: str
    points: tuple[Point, ...]
    slope: float | None

    @property
    def path(self) -> str:
        """The SVG polyline ``points`` attribute."""
        return " ".join(f"{p.x:.1f},{p.y:.1f}" for p in self.points)


@dataclass(frozen=True)
class Tick:
    """An axis tick: its position and label."""

    position: float
    label: str


@dataclass(frozen=True)
class TimingChart:
    """Everything the template needs to draw the chart."""

    width: int
    height: int
    plot_left: int
    plot_right: int
    plot_top: int
    plot_bottom: int
    x_ticks: tuple[Tick, ...]
    y_ticks: tuple[Tick, ...]
    series: tuple[Series, ...]


def _seconds_label(seconds: float) -> str:
    if seconds >= 1:
        return f"{seconds:g} s"
    if seconds >= 0.001:
        return f"{seconds * 1000:g} ms"
    return f"{seconds * 1_000_000:g} µs"


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Least-squares slope of log(y) on log(x)."""
    if len(xs) < 2:
        return None
    lx = [math.log10(x) for x in xs]
    ly = [math.log10(y) for y in ys]
    mean_x, mean_y = sum(lx) / len(lx), sum(ly) / len(ly)
    spread = sum((x - mean_x) ** 2 for x in lx)
    if spread == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(lx, ly, strict=True)) / spread


def build_timing_chart(rows: Sequence[BenchmarkRow]) -> TimingChart:
    """Lay out the engine and naive timings on log-log axes.

    Args:
        rows: Benchmark results, one per size, in increasing size order.

    Returns:
        The chart geometry.

    Raises:
        ValueError: If ``rows`` is empty.
    """
    if not rows:
        raise ValueError("Nothing to chart")
    sizes = [r.size for r in rows]
    # A timer can report zero for a very fast run; one microsecond is below
    # anything measurable here and keeps the logarithm defined.
    engine = [max(r.engine_seconds, 1e-6) for r in rows]
    naive = [max(r.naive_seconds, 1e-6) for r in rows]
    left, right = MARGIN_LEFT, WIDTH - MARGIN_RIGHT
    top, bottom = MARGIN_TOP, HEIGHT - MARGIN_BOTTOM

    x_lo, x_hi = math.log10(min(sizes)), math.log10(max(sizes))
    y_lo = math.floor(math.log10(min(engine + naive)))
    y_hi = math.ceil(math.log10(max(engine + naive)))
    if y_hi == y_lo:
        y_hi += 1

    def x_at(size: float) -> float:
        """Map an entry count to a horizontal position on the log scale."""
        span = x_hi - x_lo
        return left + (right - left) * ((math.log10(size) - x_lo) / span if span else 0.5)

    def y_at(seconds: float) -> float:
        """Map a run time to a vertical position on the log scale."""
        return bottom - (bottom - top) * (math.log10(seconds) - y_lo) / (y_hi - y_lo)

    def series(key: str, name: str, values: list[float]) -> Series:
        """Build one plotted line, with an accessible label on each point."""
        points = tuple(
            Point(
                x_at(row.size),
                y_at(value),
                f"{name}, {row.size:,} entries: {_seconds_label(round(value, 6))}"
                + (" (estimated)" if key == "naive" and row.naive_estimated else ""),
            )
            for row, value in zip(rows, values, strict=True)
        )
        return Series(key, name, points, _slope(sizes, values))

    return TimingChart(
        width=WIDTH,
        height=HEIGHT,
        plot_left=left,
        plot_right=right,
        plot_top=top,
        plot_bottom=bottom,
        x_ticks=tuple(Tick(x_at(s), f"{s:,}") for s in sizes),
        y_ticks=tuple(
            Tick(y_at(10.0**exp), _seconds_label(10.0**exp)) for exp in range(y_lo, y_hi + 1)
        ),
        series=(series("engine", "Engine", engine), series("naive", "Checking every pair", naive)),
    )
