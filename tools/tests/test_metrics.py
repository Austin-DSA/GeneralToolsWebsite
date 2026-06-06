from django.test import TestCase

from tools.LinkTree import metrics
from tools.models import LinkEvent, LinkTree, LinkTreeItem


class MetricsTests(TestCase):
    def setUp(self):
        self.tree = LinkTree.objects.create(slug="m", title="M")
        self.item = LinkTreeItem.objects.create(
            tree=self.tree, order=0, kind=LinkTreeItem.Kind.MANUAL, label="A", url="https://a.org"
        )
        # 3 web clicks, 2 QR scans; two web clicks share a visitorHash.
        for vh in ("aaaa", "aaaa", "bbbb"):
            LinkEvent.objects.create(tree=self.tree, item=self.item,
                                     source=LinkEvent.Source.WEB, visitorHash=vh)
        for _ in range(2):
            LinkEvent.objects.create(tree=self.tree, item=self.item, source=LinkEvent.Source.QR)

    def test_tree_summary_totals(self):
        s = metrics.treeSummary(self.tree)
        self.assertEqual(s["webTotal"], 3)
        self.assertEqual(s["qrTotal"], 2)
        self.assertEqual(s["grandTotal"], 5)
        self.assertEqual(s["uniqueVisitors"], 2)  # aaaa + bbbb (qr scans have no hash)
        self.assertEqual(s["topItems"][0]["total"], 5)
        self.assertEqual(s["topItems"][0]["label"], "A")

    def test_daily_series_buckets_web_and_qr(self):
        # Brittle (timing): assumes all events fall in a single "today" bucket.
        # Could flake if the suite straddles a UTC midnight. Left as-is — a real
        # fix needs time-freezing, which is out of scope for this refactor.
        series = metrics.dailySeries(LinkEvent.objects.filter(tree=self.tree))
        self.assertEqual(len(series), 1)  # all created "today"
        self.assertEqual(series[0]["web"], 3)
        self.assertEqual(series[0]["qr"], 2)
        self.assertEqual(series[0]["total"], 5)
        self.assertEqual(series[0]["pct"], 100)

    def test_overview_rows(self):
        rows = {r["tree"].slug: r for r in metrics.overviewRows()}
        self.assertEqual(rows["m"]["web"], 3)
        self.assertEqual(rows["m"]["qr"], 2)
