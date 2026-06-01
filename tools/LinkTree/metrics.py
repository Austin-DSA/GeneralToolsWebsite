"""Link Tree analytics aggregation.

Pure query/aggregation logic, kept out of the HTTP layer so it can be unit-tested
without the request cycle and reused by both the dashboard view and the CSV
export. The view just calls these and renders.
"""

import datetime

from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from ..models import LinkEvent, LinkTree

METRICS_WINDOW_DAYS = 30


def _windowStart(windowDays: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=windowDays)


def overviewRows() -> list[dict]:
    """Every tree with its lifetime web-click / QR-scan totals (for the index)."""
    counts = {
        row["tree"]: row
        for row in LinkEvent.objects.filter(tree__isnull=False)
        .values("tree")
        .annotate(
            web=Count("id", filter=Q(source=LinkEvent.Source.WEB)),
            qr=Count("id", filter=Q(source=LinkEvent.Source.QR)),
        )
    }
    return [
        {
            "tree": tree,
            "web": counts.get(tree.id, {}).get("web", 0),
            "qr": counts.get(tree.id, {}).get("qr", 0),
        }
        for tree in LinkTree.objects.all().order_by("title")
    ]


def treeSummary(tree: LinkTree) -> dict:
    """Scalar totals, top links, and per-QR scan counts for one tree."""
    events = LinkEvent.objects.filter(tree=tree)

    webTotal = events.filter(source=LinkEvent.Source.WEB).count()
    qrTotal = events.filter(source=LinkEvent.Source.QR).count()
    uniqueVisitors = events.exclude(visitorHash="").values("visitorHash").distinct().count()

    topItems = list(
        events.filter(item__isnull=False)
        .values("item__id", "item__label", "item__resolvedLabel")
        .annotate(total=Count("id"))
        .order_by("-total")[:25]
    )
    for row in topItems:
        row["label"] = (
            row["item__label"] or row["item__resolvedLabel"] or f"Item {row['item__id']}"
        )

    qrRows = list(
        events.filter(source=LinkEvent.Source.QR, qr__isnull=False)
        .values("qr__code", "qr__label", "qr__campaign")
        .annotate(scans=Count("id"))
        .order_by("-scans")
    )

    return {
        "webTotal": webTotal,
        "qrTotal": qrTotal,
        "grandTotal": webTotal + qrTotal,
        "uniqueVisitors": uniqueVisitors,
        "topItems": topItems,
        "qrRows": qrRows,
    }


def dailyEventTotals(events):
    """Per-day, per-source counts as queryset rows ({day, source, total}).

    Shared by the dashboard series and the CSV export so the group-by lives in
    one place.
    """
    return (
        events.annotate(day=TruncDate("occurredAt"))
        .values("day", "source")
        .annotate(total=Count("id"))
        .order_by("day", "source")
    )


def dailySeries(events, windowDays: int = METRICS_WINDOW_DAYS) -> list[dict]:
    """Daily web/qr/total rows for the last ``windowDays``, with a bar-width pct."""
    recent = events.filter(occurredAt__gte=_windowStart(windowDays))
    byDay: dict[datetime.date, dict[str, int]] = {}
    for row in dailyEventTotals(recent):
        bucket = byDay.setdefault(row["day"], {"web": 0, "qr": 0})
        if row["source"] == LinkEvent.Source.WEB:
            bucket["web"] += row["total"]
        else:
            bucket["qr"] += row["total"]

    maxDay = max((b["web"] + b["qr"] for b in byDay.values()), default=0)
    return [
        {
            "day": day,
            "web": byDay[day]["web"],
            "qr": byDay[day]["qr"],
            "total": byDay[day]["web"] + byDay[day]["qr"],
            "pct": round(100 * (byDay[day]["web"] + byDay[day]["qr"]) / maxDay) if maxDay else 0,
        }
        for day in sorted(byDay)
    ]
