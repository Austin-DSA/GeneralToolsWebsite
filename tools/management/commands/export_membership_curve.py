"""Dump the membership "bleeding curve" - the full MembershipSnapshot time
series - as CSV or JSON.

v1 of the payoff: enough to prove the data exists and let anyone chart it
(e.g. hand the CSV to MaineDSA's open-source membership_dashboard) without
building a bespoke charting page in Echo first.

Run from the repo root:
    python manage.py export_membership_curve --format csv > curve.csv
    python manage.py export_membership_curve --format json > curve.json
"""

import csv
import json
import sys

from django.core.management.base import BaseCommand

from tools.models import MembershipSnapshot


class Command(BaseCommand):
    help = "Dump the MembershipSnapshot series (the bleeding curve) as CSV or JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=["csv", "json"],
            default="csv",
            help="Output format (default csv).",
        )
        parser.add_argument(
            "--out",
            default=None,
            help="Write to this file instead of stdout.",
        )

    def handle(self, *args, **options):
        fmt = options["format"]
        outPath = options["out"]

        snapshots = MembershipSnapshot.objects.all().order_by("listDate")

        rows = [
            {
                "listDate": s.listDate.isoformat(),
                "goodStanding": s.goodStanding,
                "member": s.member,
                "lapsed": s.lapsed,
                "total": s.total,
                "sourceEmailDate": s.sourceEmailDate.isoformat() if s.sourceEmailDate else "",
                "ingestedAt": s.ingestedAt.isoformat(),
            }
            for s in snapshots
        ]

        outFile = open(outPath, "w", newline="", encoding="utf-8") if outPath else sys.stdout

        try:
            if fmt == "csv":
                fieldnames = ["listDate", "goodStanding", "member", "lapsed", "total", "sourceEmailDate", "ingestedAt"]
                writer = csv.DictWriter(outFile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            else:
                json.dump(rows, outFile, indent=2)
                outFile.write("\n")
        finally:
            if outPath:
                outFile.close()

        if outPath:
            self.stderr.write(self.style.SUCCESS(f"Wrote {len(rows)} row(s) to {outPath}"))
