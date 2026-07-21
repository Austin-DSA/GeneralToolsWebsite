import datetime
import io

from django.core.management import call_command
from django.test import TestCase

from tools.management.commands.seed_membership_snapshots import buildSeries
from tools.models import MembershipSnapshot


class BuildSeriesTests(TestCase):
    def test_series_is_deterministic(self):
        self.assertEqual(buildSeries(), buildSeries())

    def test_series_spans_six_years_monthly(self):
        rows = buildSeries()
        self.assertEqual(len(rows), 72)  # 2020-07 .. 2026-06 inclusive
        self.assertEqual(rows[0]["listDate"], datetime.date(2020, 7, 1))
        self.assertEqual(rows[-1]["listDate"], datetime.date(2026, 6, 1))

    def test_splits_always_sum_to_total(self):
        for row in buildSeries():
            self.assertEqual(
                row["goodStanding"] + row["member"] + row["lapsed"], row["total"],
                row["listDate"],
            )

    def test_story_shape_surge_then_bleed(self):
        rows = {r["listDate"]: r["total"] for r in buildSeries()}
        surgePeak = rows[datetime.date(2020, 11, 1)]
        # The 2020 surge climbs well above the starting point...
        self.assertGreater(surgePeak, rows[datetime.date(2020, 7, 1)] + 150)
        # ...then the post-surge bleed pulls it back down...
        self.assertLess(rows[datetime.date(2021, 8, 1)], surgePeak - 150)
        # ...and the recent slow decline leaves the end below the 2023 plateau.
        self.assertLess(rows[datetime.date(2026, 6, 1)], rows[datetime.date(2023, 8, 1)])


class SeedCommandTests(TestCase):
    def _run(self, *args):
        out = io.StringIO()
        call_command("seed_membership_snapshots", *args, quiet=True, stdout=out)
        return out.getvalue()

    def test_seeds_full_series(self):
        self._run()
        self.assertEqual(MembershipSnapshot.objects.count(), 72)

    def test_rerun_is_idempotent(self):
        self._run()
        firstTotals = list(MembershipSnapshot.objects.order_by("listDate").values_list("total", flat=True))
        self._run()
        self.assertEqual(MembershipSnapshot.objects.count(), 72)
        secondTotals = list(MembershipSnapshot.objects.order_by("listDate").values_list("total", flat=True))
        self.assertEqual(firstTotals, secondTotals)

    def test_clear_removes_preexisting_rows(self):
        MembershipSnapshot.objects.create(
            listDate=datetime.date(2010, 1, 1), goodStanding=1, member=1, lapsed=1, total=3
        )
        self._run("--clear")
        self.assertEqual(MembershipSnapshot.objects.count(), 72)
        self.assertFalse(
            MembershipSnapshot.objects.filter(listDate=datetime.date(2010, 1, 1)).exists()
        )

    def test_output_mentions_fabricated_data(self):
        out = self._run()
        self.assertIn("Demo data only", out)
