"""Chart/summary helpers for the membership metrics page.

Mirrors the LinkTree/metrics.py precedent: the view stays thin and the
aggregation lives here. Everything is computed off the MembershipSnapshot
series (already PII-free aggregates - see this package's README).

The curve is rendered as a server-computed inline SVG (points strings for
<polyline>), not a JS chart library - the app is vanilla-JS/no-frameworks by
convention, and a static polyline is all a time series needs.
"""

import dataclasses

from tools.models import MembershipSnapshot

# SVG drawing area. The template's viewBox must match.
CHART_WIDTH = 720
CHART_HEIGHT = 260
CHART_PAD_LEFT = 44   # room for y-axis labels
CHART_PAD_RIGHT = 8
CHART_PAD_TOP = 10
CHART_PAD_BOTTOM = 24  # room for x-axis labels


@dataclasses.dataclass
class Series:
    """One polyline: the SVG-ready points string for a snapshot field."""
    key: str
    label: str
    points: str


def _niceCeiling(value: int) -> int:
    """Round up to a 'nice' axis maximum so gridline labels are round numbers."""
    if value <= 0:
        return 1
    magnitude = 1
    while magnitude * 10 <= value:
        magnitude *= 10
    for mult in (1, 2, 5, 10):
        if mult * magnitude >= value:
            return mult * magnitude
    return 10 * magnitude


def curveContext() -> dict:
    """Everything membership_metrics.html needs to render the page."""
    snapshots = list(MembershipSnapshot.objects.order_by("listDate"))
    if not snapshots:
        return {"snapshots": [], "hasData": False}

    latest = snapshots[-1]
    previous = snapshots[-2] if len(snapshots) > 1 else None
    latestDelta = (latest.total - previous.total) if previous else None

    # --- SVG geometry -----------------------------------------------------
    plotWidth = CHART_WIDTH - CHART_PAD_LEFT - CHART_PAD_RIGHT
    plotHeight = CHART_HEIGHT - CHART_PAD_TOP - CHART_PAD_BOTTOM
    yMax = _niceCeiling(max(s.total for s in snapshots))
    n = len(snapshots)

    def x(i: int) -> float:
        if n == 1:
            return CHART_PAD_LEFT + plotWidth / 2
        return CHART_PAD_LEFT + (i / (n - 1)) * plotWidth

    def y(value: int) -> float:
        return CHART_PAD_TOP + (1 - value / yMax) * plotHeight

    def pointsFor(field: str) -> str:
        return " ".join(
            f"{x(i):.1f},{y(getattr(s, field)):.1f}" for i, s in enumerate(snapshots)
        )

    series = [
        Series(key="total", label="Total", points=pointsFor("total")),
        Series(key="good", label="Good standing", points=pointsFor("goodStanding")),
        Series(key="member", label="Member", points=pointsFor("member")),
        Series(key="lapsed", label="Lapsed", points=pointsFor("lapsed")),
    ]

    # Horizontal gridlines at quarter intervals of the (nice) y max.
    gridlines = []
    for frac in (0.25, 0.5, 0.75, 1.0):
        value = int(yMax * frac)
        gridlines.append({"value": value, "y": round(y(value), 1)})

    # X-axis labels: one per January (year boundaries), plus the first point.
    xLabels = []
    seenYears = set()
    for i, s in enumerate(snapshots):
        isYearStart = s.listDate.month == 1 and s.listDate.year not in seenYears
        if i == 0 or isYearStart:
            seenYears.add(s.listDate.year)
            xLabels.append({"label": str(s.listDate.year), "x": round(x(i), 1)})

    # --- Table rows: newest first, with month-over-month delta ------------
    rows = []
    for i, s in enumerate(snapshots):
        delta = (s.total - snapshots[i - 1].total) if i > 0 else None
        rows.append({"snapshot": s, "delta": delta})
    rows.reverse()

    return {
        "hasData": True,
        "snapshots": snapshots,
        "latest": latest,
        "latestDelta": latestDelta,
        "series": series,
        "gridlines": gridlines,
        "xLabels": xLabels,
        "rows": rows,
        "chartWidth": CHART_WIDTH,
        "chartHeight": CHART_HEIGHT,
        "plotLeft": CHART_PAD_LEFT,
        "plotRight": CHART_WIDTH - CHART_PAD_RIGHT,
        "plotBottom": CHART_HEIGHT - CHART_PAD_BOTTOM,
    }
