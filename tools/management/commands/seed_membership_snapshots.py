"""Seed ~6 years of FABRICATED monthly MembershipSnapshot rows for demos.

The generated series tells a plausible chapter story - a pre-2020 baseline,
the 2020 surge spike, two post-surge bleed waves, flat stretches, and a slow
recent decline - so the membership-metrics page has a demonstrable curve
without any real member data. Every number is invented.

Deterministic: a fixed random seed and a fixed date range (no wall-clock
randomness), so every run on every box produces the identical series.
Idempotent: update_or_create keyed on listDate, so re-runs overwrite in place.

Run from the repo root:
    python manage.py seed_membership_snapshots [--clear] [--quiet]
"""

import datetime
import random

from django.core.management.base import BaseCommand

from tools.models import MembershipSnapshot

# Fixed seed: the fabricated series must be identical on every run/box.
RANDOM_SEED = 20260714

FIRST_LIST_DATE = datetime.date(2020, 7, 1)
LAST_LIST_DATE = datetime.date(2026, 6, 1)


def _monthlyDates(first: datetime.date, last: datetime.date):
    current = first
    while current <= last:
        yield current
        year = current.year + (current.month // 12)
        month = current.month % 12 + 1
        current = datetime.date(year, month, 1)


def _monthlyDrift(listDate: datetime.date) -> int:
    """The story's base month-over-month change in total membership."""
    if listDate < datetime.date(2020, 11, 1):
        return 90    # mid-2020 surge: DSA's national wave hits Austin
    if listDate < datetime.date(2021, 8, 1):
        return -28   # post-surge bleed, wave 1
    if listDate < datetime.date(2022, 2, 1):
        return 0     # flat stretch
    if listDate < datetime.date(2022, 11, 1):
        return -16   # bleed, wave 2
    if listDate < datetime.date(2023, 8, 1):
        return 1     # flat stretch
    return -4        # slow decline since


def buildSeries() -> list:
    """The full fabricated series as dicts (separated from the command so
    tests can assert on the numbers without running the command)."""
    rng = random.Random(RANDOM_SEED)
    rows = []
    total = 640  # mid-2020 starting point, surge already beginning
    for listDate in _monthlyDates(FIRST_LIST_DATE, LAST_LIST_DATE):
        total = max(200, total + _monthlyDrift(listDate) + rng.randint(-8, 8))
        # Split: good standing dominates; lapsed grows in the bleed years.
        goodShare = 0.62 + rng.uniform(-0.03, 0.03)
        memberShare = 0.16 + rng.uniform(-0.02, 0.02)
        goodStanding = int(total * goodShare)
        member = int(total * memberShare)
        lapsed = total - goodStanding - member
        rows.append({
            "listDate": listDate,
            "goodStanding": goodStanding,
            "member": member,
            "lapsed": lapsed,
            "total": total,
        })
    return rows


class Command(BaseCommand):
    help = (
        "Seed FABRICATED demo-only MembershipSnapshot rows (~6 years, monthly) "
        "so the membership-metrics page has a curve to show. Deterministic and "
        "idempotent; never use on a box holding real ingested snapshots."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete ALL existing MembershipSnapshot rows first.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-row lines (the summary still prints).",
        )

    def handle(self, *args, **options):
        quiet = options["quiet"]

        if options["clear"]:
            deleted, _ = MembershipSnapshot.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing snapshot row(s)."))

        rows = buildSeries()
        created = 0
        updated = 0
        for row in rows:
            _, wasCreated = MembershipSnapshot.objects.update_or_create(
                listDate=row["listDate"],
                defaults=dict(
                    goodStanding=row["goodStanding"],
                    member=row["member"],
                    lapsed=row["lapsed"],
                    total=row["total"],
                    sourceEmailDate=None,
                ),
            )
            created += 1 if wasCreated else 0
            updated += 0 if wasCreated else 1
            if not quiet:
                self.stdout.write(
                    f"  [{'NEW' if wasCreated else 'UPD'}] {row['listDate']} total={row['total']}"
                )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(rows)} fabricated snapshots (created {created}, updated {updated}). "
            "Demo data only - not real membership numbers."
        ))
