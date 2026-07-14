import datetime

from django.test import TestCase
from django.urls import reverse

from tools.MembershipList import metrics
from tools.models import MembershipSnapshot
from tools.tests.support import LoginClientMixin, UserFactory, fastHashing


def _makeSnapshot(listDate, good=60, member=20, lapsed=20):
    return MembershipSnapshot.objects.create(
        listDate=listDate,
        goodStanding=good,
        member=member,
        lapsed=lapsed,
        total=good + member + lapsed,
    )


@fastHashing
class MembershipMetricsViewGatingTests(LoginClientMixin, TestCase):
    def setUp(self):
        self.viewer = UserFactory.make("viewer", perms=("viewMembershipMetrics",))
        self.member = UserFactory.make("plainmember")

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("membership-metrics"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_user_without_permission_is_denied(self):
        self.loginAs(self.member)
        resp = self.client.get(reverse("membership-metrics"))
        self.assertEqual(resp.status_code, 302)  # bounced by permission_required

    def test_user_with_permission_gets_page(self):
        self.loginAs(self.viewer)
        resp = self.client.get(reverse("membership-metrics"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Membership Metrics")

    def test_empty_state_renders_without_snapshots(self):
        self.loginAs(self.viewer)
        resp = self.client.get(reverse("membership-metrics"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No membership snapshots yet")

    def test_page_renders_chart_and_table_with_data(self):
        _makeSnapshot(datetime.date(2024, 1, 1), good=60, member=20, lapsed=20)
        _makeSnapshot(datetime.date(2024, 2, 1), good=55, member=20, lapsed=20)
        self.loginAs(self.viewer)
        resp = self.client.get(reverse("membership-metrics"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "curve-chart")
        self.assertContains(resp, "polyline")
        # Latest total (95) and its negative month-over-month delta (-5).
        self.assertContains(resp, "95")
        self.assertContains(resp, "-5")

    def test_nav_tile_visibility_follows_permission(self):
        # The Membership domain card only appears for users holding the perm.
        self.loginAs(self.viewer)
        self.assertContains(self.client.get(reverse("index")), "Membership Metrics")
        self.loginAs(self.member)
        self.assertNotContains(self.client.get(reverse("index")), "Membership Metrics")


class CurveContextTests(TestCase):
    def test_empty_series(self):
        context = metrics.curveContext()
        self.assertFalse(context["hasData"])

    def test_series_and_deltas(self):
        _makeSnapshot(datetime.date(2024, 1, 1), good=60, member=20, lapsed=20)   # 100
        _makeSnapshot(datetime.date(2024, 2, 1), good=50, member=20, lapsed=20)   # 90
        _makeSnapshot(datetime.date(2024, 3, 1), good=55, member=20, lapsed=20)   # 95

        context = metrics.curveContext()

        self.assertTrue(context["hasData"])
        self.assertEqual(context["latest"].total, 95)
        self.assertEqual(context["latestDelta"], 5)
        self.assertEqual([s.key for s in context["series"]], ["total", "good", "member", "lapsed"])
        # Each polyline has one point per snapshot.
        for s in context["series"]:
            self.assertEqual(len(s.points.split(" ")), 3)
        # Table rows are newest-first with the right deltas.
        deltas = [row["delta"] for row in context["rows"]]
        self.assertEqual(deltas, [5, -10, None])

    def test_single_snapshot_has_no_delta(self):
        _makeSnapshot(datetime.date(2024, 1, 1))
        context = metrics.curveContext()
        self.assertIsNone(context["latestDelta"])
        self.assertEqual(context["rows"][0]["delta"], None)
