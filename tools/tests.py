import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from tools.LinkTree import metrics, tracking
from tools.LinkTree import WikiLinkResolver
from tools.models import LinkEvent, LinkTree, LinkTreeItem, QRCode
from tools.WikiAutomation.OutlineAPI import OutlineAPI, OutlineConfig


# --- tracking (privacy-first helpers) --------------------------------------


class TrackingHelperTests(TestCase):
    def test_visitor_hash_is_deterministic_per_day(self):
        day = datetime.date(2026, 5, 31)
        h1 = tracking.visitorHash("203.0.113.5", "UA/1.0", "salt", day=day)
        h2 = tracking.visitorHash("203.0.113.5", "UA/1.0", "salt", day=day)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)

    def test_visitor_hash_rotates_daily_and_hides_ip(self):
        ip = "203.0.113.5"
        d1 = tracking.visitorHash(ip, "UA/1.0", "salt", day=datetime.date(2026, 5, 31))
        d2 = tracking.visitorHash(ip, "UA/1.0", "salt", day=datetime.date(2026, 6, 1))
        self.assertNotEqual(d1, d2, "hash must rotate across days")
        # The raw IP must not be recoverable/visible in the digest.
        self.assertNotIn("203.0.113.5", d1)

    def test_ua_family_is_coarse(self):
        self.assertEqual(
            tracking.uaFamily("Mozilla/5.0 (iPhone) AppleWebKit Safari"), "mobile-safari"
        )
        self.assertEqual(tracking.uaFamily("Mozilla/5.0 (Windows) Chrome/120"), "desktop-chrome")
        self.assertEqual(tracking.uaFamily("Googlebot/2.1"), "bot")
        self.assertEqual(tracking.uaFamily(""), "")

    def test_referrer_host_strips_path(self):
        self.assertEqual(
            tracking.referrerHost("https://twitter.com/austin_dsa/status/123"), "twitter.com"
        )
        self.assertEqual(tracking.referrerHost(""), "")

    def test_client_ip_prefers_forwarded_for(self):
        meta = {"HTTP_X_FORWARDED_FOR": "198.51.100.7, 10.0.0.1", "REMOTE_ADDR": "10.0.0.1"}
        self.assertEqual(tracking.clientIpFromMeta(meta), "198.51.100.7")
        self.assertEqual(tracking.clientIpFromMeta({"REMOTE_ADDR": "10.0.0.1"}), "10.0.0.1")


# --- wiki resolver (fake Outline) ------------------------------------------


class _FakeOutline(OutlineAPI):
    """OutlineAPI with _call stubbed to canned responses keyed by method."""

    def __init__(self, responses):
        super().__init__(OutlineConfig(baseUrl="https://wiki.example.org", apiToken="t"))
        self._responses = responses

    def _call(self, method, payload):
        return self._responses[method]


class WikiResolverTests(TestCase):
    def test_resolve_latest_picks_newest_title_match(self):
        api = _FakeOutline({
            "documents.search": {"data": [
                {"document": {"id": "a", "title": "2026-04-01 GBM Agenda",
                              "publishedAt": "2026-04-01T00:00:00Z", "updatedAt": "2026-04-01T00:00:00Z",
                              "url": "/doc/apr"}},
                {"document": {"id": "b", "title": "2026-05-01 GBM Agenda",
                              "publishedAt": "2026-05-01T00:00:00Z", "updatedAt": "2026-05-02T00:00:00Z",
                              "url": "/doc/may"}},
                {"document": {"id": "c", "title": "Some unrelated note",
                              "publishedAt": "2026-06-01T00:00:00Z", "updatedAt": "2026-06-01T00:00:00Z",
                              "url": "/doc/other"}},
            ]},
        })
        result = WikiLinkResolver.resolveLatest(api, "GBM Agenda")
        self.assertIsNotNone(result)
        self.assertEqual(result.url, "https://wiki.example.org/doc/may")
        self.assertEqual(result.title, "2026-05-01 GBM Agenda")

    def test_resolve_latest_ignores_drafts_and_non_title_matches(self):
        api = _FakeOutline({
            "documents.search": {"data": [
                {"document": {"id": "d", "title": "GBM Agenda draft",
                              "publishedAt": None, "updatedAt": "2026-07-01T00:00:00Z", "url": "/doc/d"}},
            ]},
        })
        self.assertIsNone(WikiLinkResolver.resolveLatest(api, "GBM Agenda"))

    def test_resolve_pinned(self):
        api = _FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
        })
        result = WikiLinkResolver.resolvePinned(api, "x")
        self.assertEqual(result.url, "https://wiki.example.org/doc/onboarding")
        self.assertEqual(result.title, "Onboarding")


# --- model invariants ------------------------------------------------------


class QRCodeModelTests(TestCase):
    def setUp(self):
        self.tree = LinkTree.objects.create(slug="links", title="Links")

    def test_exactly_one_target_required(self):
        with self.assertRaises(ValidationError):
            QRCode(code="none", label="No target").full_clean()
        with self.assertRaises(ValidationError):
            QRCode(code="two", label="Two", tree=self.tree, rawUrl="https://x.org").full_clean()
        # One target is fine.
        QRCode(code="one", label="One", tree=self.tree).full_clean()

    def test_target_url_resolution_order(self):
        qr = QRCode.objects.create(code="t", label="T", tree=self.tree)
        self.assertEqual(qr.targetUrl(), self.tree.getPublicUrl())

    def test_resolve_target_returns_destination_and_attribution(self):
        item = LinkTreeItem.objects.create(
            tree=self.tree, order=0, kind=LinkTreeItem.Kind.MANUAL,
            label="X", url="https://example.org/x",
        )
        # Tree target → attribute to the tree, no item.
        dest, tree, it = QRCode(code="qt", label="qt", tree=self.tree).resolveTarget()
        self.assertEqual((dest, tree, it), (self.tree.getPublicUrl(), self.tree, None))
        # Item target → destination is the item's url, attributed to item + its tree.
        dest, tree, it = QRCode(code="qi", label="qi", item=item).resolveTarget()
        self.assertEqual((dest, tree, it), ("https://example.org/x", self.tree, item))
        # Raw url → no tree/item attribution.
        dest, tree, it = QRCode(code="qr", label="qr", rawUrl="https://raw.example").resolveTarget()
        self.assertEqual((dest, tree, it), ("https://raw.example", None, None))


class LinkTreeItemTests(TestCase):
    def test_header_has_no_tracked_url(self):
        tree = LinkTree.objects.create(slug="h", title="H")
        header = LinkTreeItem.objects.create(
            tree=tree, order=0, kind=LinkTreeItem.Kind.SECTION_HEADER, label="Section",
        )
        self.assertIsNone(header.trackedUrl())
        self.assertTrue(header.shouldDisplay())  # headers always show
        self.assertFalse(header.isResolved())


# --- public views & tracking-through-the-site ------------------------------


class PublicViewTests(TestCase):
    def setUp(self):
        self.public = LinkTree.objects.create(
            slug="links", title="Austin DSA", visibility=LinkTree.Visibility.PUBLIC
        )
        self.item = LinkTreeItem.objects.create(
            tree=self.public, order=0, kind=LinkTreeItem.Kind.MANUAL,
            label="Join", url="https://example.org/join",
        )
        self.members = LinkTree.objects.create(
            slug="members", title="Members", visibility=LinkTree.Visibility.MEMBERS
        )

    def test_public_tree_renders_without_login(self):
        resp = self.client.get(reverse("link-tree", kwargs={"slug": "links"}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Join")
        # The button points at the tracked /go/ endpoint, not the raw url.
        self.assertContains(resp, reverse("link-go", kwargs={"item_id": self.item.pk}))
        self.assertNotContains(resp, "https://example.org/join")

    def test_members_tree_redirects_to_login(self):
        resp = self.client.get(reverse("link-tree", kwargs={"slug": "members"}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_inactive_tree_is_404(self):
        self.public.isActive = False
        self.public.save()
        resp = self.client.get(reverse("link-tree", kwargs={"slug": "links"}))
        self.assertEqual(resp.status_code, 404)

    def test_go_logs_web_event_and_redirects_to_stored_destination(self):
        url = reverse("link-go", kwargs={"item_id": self.item.pk})
        # An attacker-supplied ?next must be ignored — no open redirect.
        resp = self.client.get(url + "?next=https://evil.example.com")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://example.org/join")
        event = LinkEvent.objects.get()
        self.assertEqual(event.source, LinkEvent.Source.WEB)
        self.assertEqual(event.item, self.item)
        self.assertEqual(event.tree, self.public)
        self.assertEqual(event.destinationUrl, "https://example.org/join")

    def test_qr_redirect_logs_scan_and_is_repointable(self):
        qr = QRCode.objects.create(code="flyer", label="Flyer", item=self.item, campaign="tabling")
        url = reverse("qr-redirect", kwargs={"code": "flyer"})

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://example.org/join")
        self.assertEqual(LinkEvent.objects.filter(source=LinkEvent.Source.QR).count(), 1)

        # Repoint the SAME code at a different item; the code/URL is unchanged.
        other = LinkTreeItem.objects.create(
            tree=self.public, order=1, kind=LinkTreeItem.Kind.MANUAL,
            label="Donate", url="https://example.org/donate",
        )
        qr.item = other
        qr.save()
        resp2 = self.client.get(url)
        self.assertEqual(resp2["Location"], "https://example.org/donate")
        self.assertEqual(LinkEvent.objects.filter(source=LinkEvent.Source.QR).count(), 2)

    def test_qr_redirect_into_members_tree_gates_anonymous_scanner(self):
        # A QR whose target is an item in a MEMBERS tree must not 302 an
        # anonymous scanner straight to the destination — it gates to login,
        # the same as the /t/ page and /go/ click, and logs no scan event.
        members_item = LinkTreeItem.objects.create(
            tree=self.members, order=0, kind=LinkTreeItem.Kind.MANUAL,
            label="Internal", url="https://example.org/internal",
        )
        qr = QRCode.objects.create(code="internal", label="Internal", item=members_item)
        resp = self.client.get(reverse("qr-redirect", kwargs={"code": "internal"}))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])
        self.assertNotIn("example.org/internal", resp["Location"])
        self.assertEqual(LinkEvent.objects.count(), 0)

    def test_unresolved_wiki_item_is_hidden_not_dead(self):
        LinkTreeItem.objects.create(
            tree=self.public, order=2, kind=LinkTreeItem.Kind.WIKI,
            label="Latest GBM agenda", wikiQuery="GBM Agenda",  # not yet resolved
        )
        resp = self.client.get(reverse("link-tree", kwargs={"slug": "links"}))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Latest GBM agenda")

    def test_section_header_renders_as_heading_not_link(self):
        LinkTreeItem.objects.create(
            tree=self.public, order=1, kind=LinkTreeItem.Kind.SECTION_HEADER,
            label="Resolutions",
        )
        resp = self.client.get(reverse("link-tree", kwargs={"slug": "links"}))
        self.assertEqual(resp.status_code, 200)
        # The header text shows, but it is NOT a tracked /go/ link.
        self.assertContains(resp, "Resolutions")
        self.assertContains(resp, "lt-header")


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
