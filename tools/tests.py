import datetime
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tools.LinkTree import metrics, tracking
from tools.LinkTree import WikiLinkResolver
from tools.models import LinkEvent, LinkTree, LinkTreeItem, NotifiedHeldNote, QRCode
from tools.WikiAutomation import LCNotePublisher
from tools.WikiAutomation.LCNotePublisher import Outcome, sweep
from tools.WikiAutomation.OutlineAPI import (
    OutlineAPI,
    OutlineAPIError,
    OutlineConfig,
    OutlineDocument,
)



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
    """OutlineAPI with _call stubbed to canned responses keyed by method.

    A canned value that is an Exception instance is raised instead of returned
    (for failure-path tests). Calls are recorded on ``self.calls``.
    """

    def __init__(self, responses):
        super().__init__(OutlineConfig(baseUrl="https://wiki.example.org", apiToken="t"))
        self._responses = responses
        self.calls = []

    def _call(self, method, payload):
        self.calls.append((method, payload))
        response = self._responses[method]
        if isinstance(response, Exception):
            raise response
        return response


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
            "shares.create": {"data": {"id": "sh-may", "url": "https://wiki.example.org/s/may-share",
                                       "published": True}},
        })
        result = WikiLinkResolver.resolveLatest(api, "GBM Agenda")
        self.assertIsNotNone(result)
        # The winner's PUBLISHED SHARE url (no wiki login), not the direct /doc/ url.
        self.assertEqual(result.url, "https://wiki.example.org/s/may-share")
        self.assertEqual(result.title, "2026-05-01 GBM Agenda")
        self.assertIn(("shares.create", {"documentId": "b"}), api.calls)
        # Already published — no shares.update needed.
        self.assertNotIn("shares.update", [method for method, _ in api.calls])

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
            "shares.create": {"data": {"id": "sh-x", "url": "https://wiki.example.org/s/onboarding",
                                       "published": True}},
        })
        result = WikiLinkResolver.resolvePinned(api, "x")
        self.assertEqual(result.url, "https://wiki.example.org/s/onboarding")
        self.assertEqual(result.title, "Onboarding")

    def test_resolver_publishes_an_unpublished_share(self):
        # shares.create is get-or-create; an existing-but-unpublished share must
        # be published (shares.update) before its url is usable without login.
        api = _FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
            "shares.create": {"data": {"id": "sh-x", "url": "https://wiki.example.org/s/onboarding",
                                       "published": False}},
            "shares.update": {"data": {"id": "sh-x", "published": True}},
        })
        result = WikiLinkResolver.resolvePinned(api, "x")
        self.assertEqual(result.url, "https://wiki.example.org/s/onboarding")
        self.assertIn(("shares.update", {"id": "sh-x", "published": True}), api.calls)

    def test_resolver_skips_share_creation_when_disabled(self):
        # createShares=False (the dry-run path) must be side-effect-free on
        # Outline: no shares.* calls at all, direct URL returned. The fake has
        # no shares.create response, so any attempt would KeyError.
        api = _FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
        })
        result = WikiLinkResolver.resolvePinned(api, "x", createShares=False)
        self.assertEqual(result.url, "https://wiki.example.org/doc/onboarding")
        self.assertEqual([method for method, _ in api.calls], ["documents.info"])

    def test_resolver_falls_back_to_direct_url_when_sharing_fails(self):
        # Missing scope / sharing disabled must never kill resolution — the
        # direct /doc/ url is no worse than not sharing at all.
        api = _FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
            "shares.create": OutlineAPIError("shares.create", 403, "missing scope"),
        })
        result = WikiLinkResolver.resolvePinned(api, "x")
        self.assertIsNotNone(result)
        self.assertEqual(result.url, "https://wiki.example.org/doc/onboarding")


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


# ===========================================================================
# LC-note auto-publishing (OutlineAPI drafts client / LCNotePublisher driver /
# publish_lc_notes command)
#
# Outline HTTP is never hit: the pure client (OutlineAPI) is exercised by
# overriding ``_call``, and the driver (LCNotePublisher.sweep) is exercised
# with a fake api object. The full command is covered by call_command
# integration tests that patch ``OutlineAPI._call`` and the SMTP helper.
#
# Precondition: the test runner boots Django, which imports SecretManager at
# settings-load. Run under DEBUG=True so the devSecrets stubs satisfy that
# import.
# ===========================================================================

KEYWORDS = ["executive session", "confidential"]


def makeDoc(docId, title="Untitled", text="", email="author@example.com", authorId="u1", published=False):
    return OutlineDocument(
        id=docId,
        title=title,
        published=published,
        authorEmail=email,
        authorId=authorId,
        text=text,
        url=f"/doc/{docId}",
    )


class FakeOutlineAPI:
    """Stand-in for OutlineAPI used to drive sweep() without HTTP."""

    def __init__(self, drafts, listRaises=False, getDocRaisesFor=None, userEmail=None, fallbackBody="fetched body"):
        self._drafts = drafts
        self._listRaises = listRaises
        self._getDocRaisesFor = getDocRaisesFor or set()
        self._userEmail = userEmail
        self._fallbackBody = fallbackBody
        self.published = []
        self.userInfoCalls = []
        self.getDocumentCalls = []

    def listDrafts(self):
        if self._listRaises:
            raise OutlineAPIError("documents.drafts", 500, "boom")
        return list(self._drafts)

    def getDocument(self, documentId):
        self.getDocumentCalls.append(documentId)
        if documentId in self._getDocRaisesFor:
            raise OutlineAPIError("documents.info", 404, "not found")
        return makeDoc(documentId, text=self._fallbackBody)

    def publishDocument(self, documentId):
        self.published.append(documentId)

    def getUserEmail(self, userId):
        self.userInfoCalls.append(userId)
        return self._userEmail

    def absoluteDocUrl(self, urlPath, documentId):
        return f"https://wiki.example.org{urlPath or '/doc/' + documentId}"


def alwaysFirst(_docId):
    return True


def neverFirst(_docId):
    return False


def recordingNotifier(sink, success=True):
    def _notify(note):
        sink.append(note.docId)
        return success
    return _notify


# --------------------------------------------------------------------------
# Keyword scan
# --------------------------------------------------------------------------
class FindExecSessionHitsTests(SimpleTestCase):
    def test_matches_case_insensitively(self):
        self.assertEqual(
            LCNotePublisher.findExecSessionHits("We entered EXECUTIVE Session now", KEYWORDS),
            ["executive session"],
        )

    def test_returns_all_matched_keywords(self):
        hits = LCNotePublisher.findExecSessionHits("confidential executive session", KEYWORDS)
        self.assertCountEqual(hits, ["executive session", "confidential"])

    def test_no_match_returns_empty(self):
        self.assertEqual(LCNotePublisher.findExecSessionHits("ordinary minutes", KEYWORDS), [])

    def test_none_text_is_safe(self):
        self.assertEqual(LCNotePublisher.findExecSessionHits(None, KEYWORDS), [])


# --------------------------------------------------------------------------
# Driver: sweep() classification
# --------------------------------------------------------------------------
class SweepTests(SimpleTestCase):
    def _sweep(self, api, publishEnabled=True, notifier=None, isFirst=alwaysFirst, titlePattern=r".*"):
        # Default titlePattern matches every title so these tests exercise
        # classification in isolation; title filtering has its own test below.
        return sweep(
            api=api,
            titlePattern=titlePattern,
            keywords=KEYWORDS,
            publishEnabled=publishEnabled,
            notifier=notifier or (lambda note: True),
            isFirstNotification=isFirst,
        )

    def test_only_drafts_matching_title_pattern_are_processed(self):
        api = FakeOutlineAPI([
            makeDoc("m1", title="2026-03-01 LC Minutes", text="ordinary"),
            makeDoc("x1", title="My grocery list", text="ordinary"),
        ])
        result = self._sweep(api, titlePattern=r"lc minutes")
        # Only the LC Minutes draft is acted on; the unrelated draft is ignored.
        self.assertEqual([n.docId for n in result.published], ["m1"])
        self.assertEqual(api.published, ["m1"])

    def test_clean_draft_is_published(self):
        api = FakeOutlineAPI([makeDoc("d1", text="ordinary minutes")])
        result = self._sweep(api)
        self.assertEqual(api.published, ["d1"])
        self.assertEqual(len(result.published), 1)
        self.assertEqual(result.published[0].outcome, Outcome.PUBLISHED)
        self.assertEqual(result.held, [])

    def test_flagged_draft_is_held_and_notified_on_first_detection(self):
        sink = []
        api = FakeOutlineAPI([makeDoc("d2", text="motion to enter executive session")])
        result = self._sweep(api, notifier=recordingNotifier(sink))
        self.assertEqual(api.published, [])  # never published
        self.assertEqual(len(result.held), 1)
        self.assertEqual(result.held[0].matchedKeywords, ["executive session"])
        self.assertTrue(result.held[0].notificationSent)
        self.assertEqual(sink, ["d2"])

    def test_flagged_draft_held_but_not_notified_when_not_first(self):
        sink = []
        api = FakeOutlineAPI([makeDoc("d2", text="executive session")])
        result = self._sweep(api, notifier=recordingNotifier(sink), isFirst=neverFirst)
        self.assertEqual(len(result.held), 1)
        self.assertFalse(result.held[0].notificationSent)
        self.assertEqual(sink, [])  # notifier never invoked

    def test_dry_run_publishes_nothing(self):
        api = FakeOutlineAPI([makeDoc("d1", text="ordinary minutes")])
        self._sweep(api, publishEnabled=False)
        self.assertEqual(api.published, [])

    def test_author_email_from_list_skips_users_info(self):
        api = FakeOutlineAPI([makeDoc("d1", text="minutes", email="known@example.com")])
        self._sweep(api)
        self.assertEqual(api.userInfoCalls, [])

    def test_missing_author_email_falls_back_to_users_info(self):
        api = FakeOutlineAPI(
            [makeDoc("d1", text="minutes", email=None)], userEmail="resolved@example.com"
        )
        result = self._sweep(api)
        self.assertEqual(api.userInfoCalls, ["u1"])
        self.assertEqual(result.published[0].authorEmail, "resolved@example.com")

    def test_empty_body_triggers_getDocument_fallback(self):
        api = FakeOutlineAPI([makeDoc("d1", text="")])
        self._sweep(api)
        self.assertEqual(api.getDocumentCalls, ["d1"])

    def test_per_doc_error_does_not_abort_sweep(self):
        api = FakeOutlineAPI(
            [makeDoc("bad", text=""), makeDoc("good", text="ordinary minutes")],
            getDocRaisesFor={"bad"},
        )
        result = self._sweep(api)
        self.assertEqual([n.docId for n in result.errored], ["bad"])
        self.assertEqual([n.docId for n in result.published], ["good"])

    def test_listing_failure_is_fatal_and_skips_notifications(self):
        sink = []
        api = FakeOutlineAPI([], listRaises=True)
        result = self._sweep(api, notifier=recordingNotifier(sink))
        self.assertIsNotNone(result.fatalError)
        self.assertEqual(result.published, [])
        self.assertEqual(sink, [])

    def test_notifier_failure_marks_notification_not_sent(self):
        api = FakeOutlineAPI([makeDoc("d2", text="executive session")])
        result = self._sweep(api, notifier=recordingNotifier([], success=False))
        self.assertFalse(result.held[0].notificationSent)

    def test_invalid_title_pattern_is_fatal_not_a_crash(self):
        api = FakeOutlineAPI([makeDoc("d1", text="ordinary minutes")])
        result = self._sweep(api, titlePattern="[unclosed")  # invalid regex
        self.assertIsNotNone(result.fatalError)
        self.assertIn("Invalid", result.fatalError)
        self.assertEqual(api.published, [])  # nothing acted on

    def test_empty_body_after_fallback_is_handled_safely(self):
        # getDocument fallback returns a None body; the scan must not crash and
        # (title being clean) the note is published — only the title was scanned.
        api = FakeOutlineAPI([makeDoc("d1", title="2026 LC Minutes", text="")], fallbackBody=None)
        result = self._sweep(api)
        self.assertEqual(api.getDocumentCalls, ["d1"])
        self.assertEqual([n.docId for n in result.published], ["d1"])


# --------------------------------------------------------------------------
# Client: listDrafts pagination + payload
# --------------------------------------------------------------------------
class ListDraftsTests(SimpleTestCase):
    def test_uses_drafts_endpoint_drops_published_and_paginates(self):
        # This instance only surfaces drafts via documents.drafts (documents.list
        # omits them, statusFilter 500s, and the collectionId filter is ignored
        # because drafts have no collection). listDrafts takes no collection arg.
        def draftDoc(i, published=False):
            doc = {"id": f"p1-{i}", "title": "t", "createdBy": {}}
            if published:
                doc["publishedAt"] = "2026-01-01T00:00:00Z"
            return doc

        page1 = [draftDoc(i) for i in range(100)]
        page1[0] = draftDoc(0, published=True)  # defensively dropped
        pages = [
            {"data": page1},
            {"data": [draftDoc(0)]},  # one more draft on page 2
        ]
        calls = []

        class RecordingAPI(OutlineAPI):
            def _call(self, method, payload):
                calls.append((method, payload))
                return pages[payload["offset"] // 100]

        api = RecordingAPI(OutlineConfig(baseUrl="https://x", apiToken="t"))
        drafts = api.listDrafts()

        self.assertEqual(calls[0][0], "documents.drafts")
        self.assertNotIn("statusFilter", calls[0][1])  # unsupported on this instance
        self.assertNotIn("collectionId", calls[0][1])  # endpoint ignores it; drafts have none
        self.assertEqual(calls[1][1]["offset"], 100)  # paginated on raw page size
        # 99 drafts from page 1 (one published dropped) + 1 from page 2 = 100
        self.assertEqual(len(drafts), 100)
        self.assertTrue(all(not d.published for d in drafts))


# --------------------------------------------------------------------------
# Model + full command integration
# --------------------------------------------------------------------------
class PublishCommandIntegrationTests(TestCase):
    def setUp(self):
        # Titles follow the real "<YYYY-MM-DD> LC Minutes" convention so they
        # match the default title pattern. The grocery-list draft does NOT match
        # and must be left untouched. createdBy has no email (like the live
        # drafts endpoint), so the command resolves it via users.info.
        self.listData = [
            {"id": "clean1", "title": "2026-03-01 LC Minutes", "text": "ordinary business",
             "url": "/doc/clean1", "createdBy": {"id": "u1"}},
            {"id": "held1", "title": "2026-04-01 LC Minutes", "text": "moved into executive session",
             "url": "/doc/held1", "createdBy": {"id": "u1"}},
            {"id": "skip1", "title": "Grocery list", "text": "milk, eggs",
             "url": "/doc/skip1", "createdBy": {"id": "u1"}},
        ]

    def _fakeCall(self):
        listData = self.listData

        def _call(_self, method, payload):
            if method == "documents.drafts":
                # single page (fewer than the page size)
                return {"data": listData if payload["offset"] == 0 else []}
            if method in ("documents.update", "users.info"):
                return {"data": {}}
            raise AssertionError(f"unexpected method {method}")

        return _call

    def test_publishes_clean_holds_flagged_and_dedupes_notifications(self):
        emails = []

        def fakeSend(toAddress, subject, messageText, attachments=None):
            emails.append((toAddress, subject))

        with mock.patch.object(OutlineAPI, "_call", self._fakeCall()), \
             mock.patch("tools.management.commands.publish_lc_notes.EmailApi.sendEmailFromWebsiteAccount", side_effect=fakeSend):
            call_command("publish_lc_notes")

            # held1 recorded exactly once, with a notification
            self.assertEqual(NotifiedHeldNote.objects.count(), 1)
            record = NotifiedHeldNote.objects.get(docId="held1")
            self.assertEqual(record.notifyCount, 1)
            # The held-note email is the one addressed to the author ("Action needed");
            # the run-summary email also mentions "held" so filter on the distinctive phrase.
            held_emails = [e for e in emails if "action needed" in e[1].lower()]
            self.assertEqual(len(held_emails), 1)

            emails.clear()
            # Second run within 7 days: held note must NOT be re-notified.
            call_command("publish_lc_notes")
            self.assertEqual(NotifiedHeldNote.objects.get(docId="held1").notifyCount, 1)
            self.assertEqual([e for e in emails if "action needed" in e[1].lower()], [])

    def test_reminder_fires_at_exactly_seven_days(self):
        def fakeSend(toAddress, subject, messageText, attachments=None):
            pass

        with mock.patch.object(OutlineAPI, "_call", self._fakeCall()), \
             mock.patch("tools.management.commands.publish_lc_notes.EmailApi.sendEmailFromWebsiteAccount", side_effect=fakeSend):
            call_command("publish_lc_notes")
            # Age to EXACTLY 7 days (update() bypasses auto_now). The next run's
            # now() is a hair later, so elapsed >= 7d and the reminder fires.
            NotifiedHeldNote.objects.filter(docId="held1").update(
                lastNotifiedAt=timezone.now() - datetime.timedelta(days=7)
            )
            call_command("publish_lc_notes")
            self.assertEqual(NotifiedHeldNote.objects.get(docId="held1").notifyCount, 2)

    def test_no_reminder_before_seven_days(self):
        emails = []

        def fakeSend(toAddress, subject, messageText, attachments=None):
            emails.append(subject)

        with mock.patch.object(OutlineAPI, "_call", self._fakeCall()), \
             mock.patch("tools.management.commands.publish_lc_notes.EmailApi.sendEmailFromWebsiteAccount", side_effect=fakeSend):
            call_command("publish_lc_notes")
            # Age to 6 days — still inside the weekly window, so no re-notify.
            NotifiedHeldNote.objects.filter(docId="held1").update(
                lastNotifiedAt=timezone.now() - datetime.timedelta(days=6)
            )
            emails.clear()
            call_command("publish_lc_notes")
            self.assertEqual(NotifiedHeldNote.objects.get(docId="held1").notifyCount, 1)
            self.assertEqual([s for s in emails if "action needed" in s.lower()], [])

    def test_dry_run_publishes_nothing_and_sends_no_email(self):
        sent = []

        def fakeSend(toAddress, subject, messageText, attachments=None):
            sent.append(subject)

        updateCalls = []
        listData = self.listData

        def _call(_self, method, payload):
            if method == "documents.drafts":
                return {"data": listData if payload["offset"] == 0 else []}
            if method == "documents.update":
                updateCalls.append(payload)
                return {"data": {}}
            return {"data": {}}

        with mock.patch.object(OutlineAPI, "_call", _call), \
             mock.patch("tools.management.commands.publish_lc_notes.EmailApi.sendEmailFromWebsiteAccount", side_effect=fakeSend):
            call_command("publish_lc_notes", "--dry-run")

        self.assertEqual(updateCalls, [])  # nothing published
        self.assertEqual(sent, [])  # no emails
        self.assertEqual(NotifiedHeldNote.objects.count(), 0)
