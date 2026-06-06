import datetime
import re
from unittest import mock

from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from tools import permissions
from tools.LinkTree import metrics, tracking
from tools.LinkTree import WikiLinkResolver
from tools.models import AccessRequests, LinkEvent, LinkTree, LinkTreeItem, QRCode, User
from tools.WikiAutomation.OutlineAPI import OutlineAPI, OutlineAPIError, OutlineConfig


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


# --- auth & access requests --------------------------------------------------
#
# The Django test runner swaps the email backend to locmem (assert via
# django.core.mail.outbox) and allows the 'testserver' host automatically.


def _permission(codename: str) -> Permission:
    return Permission.objects.get(codename=codename, content_type__app_label="tools")


def _makeUser(username: str, email: str = None, password: str = "s3cure-pw-123", **kwargs) -> User:
    return User.objects.create_user(
        username=username,
        email=email or f"{username}@example.com",
        password=password,
        **kwargs,
    )


class RegistrationTests(TestCase):
    def _validData(self, **overrides):
        data = {
            "username": "newcomer",
            "first_name": "New",
            "last_name": "Comer",
            "email": "newcomer@example.com",
            "password1": "s3cure-pw-123",
            "password2": "s3cure-pw-123",
        }
        data.update(overrides)
        return data

    def test_register_page_renders_for_anonymous(self):
        resp = self.client.get(reverse("register"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "form")

    def test_register_creates_active_user_with_no_permissions_and_logs_in(self):
        resp = self.client.post(reverse("register"), self._validData())
        self.assertRedirects(resp, "/")
        user = User.objects.get(username="newcomer")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.groups.count(), 0)
        self.assertEqual(user.user_permissions.count(), 0)
        # Auto-login happened
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)

    def test_register_rejects_duplicate_username(self):
        _makeUser("newcomer")
        resp = self.client.post(reverse("register"), self._validData())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(username="newcomer").count(), 1)

    def test_register_rejects_duplicate_email_case_insensitive(self):
        _makeUser("existing", email="newcomer@example.com")
        resp = self.client.post(
            reverse("register"), self._validData(email="NEWCOMER@example.com")
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newcomer").exists())

    def test_register_enforces_password_validators(self):
        resp = self.client.post(
            reverse("register"), self._validData(password1="12345678", password2="12345678")
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newcomer").exists())

    def test_register_redirects_authenticated_users_away(self):
        self.client.force_login(_makeUser("alreadyhere"))
        resp = self.client.get(reverse("register"))
        self.assertRedirects(resp, "/")


class AuthViewTests(TestCase):
    def test_login_page_renders(self):
        resp = self.client.get("/accounts/login/")
        self.assertEqual(resp.status_code, 200)

    def test_login_without_next_redirects_home(self):
        _makeUser("loginuser")
        resp = self.client.post(
            "/accounts/login/", {"username": "loginuser", "password": "s3cure-pw-123"}
        )
        self.assertRedirects(resp, "/")

    def test_password_reset_flow(self):
        _makeUser("forgetful", email="forgetful@example.com")
        resp = self.client.get("/accounts/password_reset/")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(
            "/accounts/password_reset/", {"email": "forgetful@example.com"}
        )
        self.assertRedirects(resp, "/accounts/password_reset/done/")
        self.assertEqual(len(mail.outbox), 1)

        # Follow the emailed confirm link through to the set-password form.
        match = re.search(r"https?://testserver(/accounts/reset/[^\s]+)", mail.outbox[0].body)
        self.assertIsNotNone(match, "reset link missing from email body")
        resp = self.client.get(match.group(1), follow=True)
        self.assertEqual(resp.status_code, 200)
        setPasswordUrl = resp.redirect_chain[-1][0]
        resp = self.client.post(
            setPasswordUrl,
            {"new_password1": "an0ther-pw-456", "new_password2": "an0ther-pw-456"},
        )
        self.assertRedirects(resp, "/accounts/reset/done/")

    def test_password_change_requires_login_and_works(self):
        resp = self.client.get("/accounts/password_change/")
        self.assertEqual(resp.status_code, 302)  # bounced to login

        self.client.force_login(_makeUser("changer"))
        resp = self.client.get("/accounts/password_change/")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(
            "/accounts/password_change/",
            {
                "old_password": "s3cure-pw-123",
                "new_password1": "an0ther-pw-456",
                "new_password2": "an0ther-pw-456",
            },
        )
        self.assertRedirects(resp, "/accounts/password_change/done/")

    def test_logout_is_post_only_and_renders(self):
        self.client.force_login(_makeUser("leaver"))
        resp = self.client.get("/accounts/logout/")
        self.assertEqual(resp.status_code, 405)  # Django 5: GET logout removed
        resp = self.client.post("/accounts/logout/")
        self.assertEqual(resp.status_code, 200)


class AccessRequestCreateTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Anti-ICE Campaign")
        self.requester = _makeUser("requester")
        self.member = _makeUser("member")
        self.member.groups.add(self.group)
        self.admin = _makeUser("admin", is_superuser=True)
        self.approver = _makeUser("approver")
        self.approver.user_permissions.add(_permission("approveAccessRequest"))

    def _post(self, target, justification="I work on this campaign."):
        return self.client.post(
            reverse("request-access"),
            {"target": target, "justification": justification},
        )

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("request-access"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_group_request_creates_row(self):
        self.client.force_login(self.requester)
        resp = self._post(f"g:{self.group.id}")
        self.assertEqual(resp.status_code, 200)
        request = AccessRequests.objects.get()
        self.assertEqual(request.status, AccessRequests.Status.REQUESTED)
        self.assertEqual(request.requester, self.requester)
        self.assertEqual(request.group, self.group)
        self.assertIsNone(request.permission)
        self.assertEqual(request.justification, "I work on this campaign.")
        self.assertIsNotNone(request.dateCreated)
        self.assertIsNone(request.dateReviewed)

    def test_group_request_emails_admins_approvers_and_members_once_each(self):
        # admin is also a group member — must still get exactly one email
        self.admin.groups.add(self.group)
        self.client.force_login(self.requester)
        self._post(f"g:{self.group.id}")

        recipients = [address for m in mail.outbox for address in m.to]
        self.assertEqual(recipients.count(self.admin.email), 1)
        self.assertEqual(recipients.count(self.member.email), 1)
        self.assertEqual(recipients.count(self.approver.email), 1)
        # requester gets a confirmation
        self.assertEqual(recipients.count(self.requester.email), 1)

        request = AccessRequests.objects.get()
        reviewPath = reverse("review-access-request", kwargs={"id": request.id})
        approverMails = [m for m in mail.outbox if self.member.email in m.to]
        self.assertIn(reviewPath, approverMails[0].body)

    def test_permission_request_does_not_email_plain_group_members(self):
        self.client.force_login(self.requester)
        permission = _permission("manageLinkTree")
        self._post(f"p:{permission.id}")

        recipients = [address for m in mail.outbox for address in m.to]
        self.assertNotIn(self.member.email, recipients)
        self.assertIn(self.admin.email, recipients)
        self.assertIn(self.approver.email, recipients)

        request = AccessRequests.objects.get()
        self.assertEqual(request.permission, permission)
        self.assertIsNone(request.group)

    def test_existing_member_cannot_request_their_group(self):
        self.client.force_login(self.member)
        resp = self._post(f"g:{self.group.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccessRequests.objects.count(), 0)

    def test_duplicate_pending_request_is_rejected(self):
        self.client.force_login(self.requester)
        self._post(f"g:{self.group.id}")
        resp = self._post(f"g:{self.group.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccessRequests.objects.count(), 1)

    def test_email_failure_does_not_fail_request_creation(self):
        self.client.force_login(self.requester)
        with mock.patch("tools.accessViews.send_mail", side_effect=Exception("smtp down")):
            resp = self._post(f"g:{self.group.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccessRequests.objects.count(), 1)


class AccessRequestReviewTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Anti-ICE Campaign")
        self.otherGroup = Group.objects.create(name="Other Campaign")
        self.requester = _makeUser("requester")
        self.member = _makeUser("member")
        self.member.groups.add(self.group)
        self.otherMember = _makeUser("othermember")
        self.otherMember.groups.add(self.otherGroup)
        self.admin = _makeUser("admin", is_superuser=True)
        self.approver = _makeUser("approver")
        self.approver.user_permissions.add(_permission("approveAccessRequest"))
        self.groupRequest = AccessRequests.objects.create(
            requester=self.requester,
            group=self.group,
            justification="please",
            status=AccessRequests.Status.REQUESTED,
        )

    def _reviewUrl(self, request=None):
        return reverse(
            "review-access-request", kwargs={"id": (request or self.groupRequest).id}
        )

    def _approve(self, reason="welcome aboard"):
        return self.client.post(self._reviewUrl(), {"approve": "YES", "reason": reason})

    def _deny(self, reason="not yet"):
        return self.client.post(self._reviewUrl(), {"approve": "NO", "reason": reason})

    def test_random_user_cannot_review(self):
        self.client.force_login(_makeUser("random"))
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.REQUESTED)
        self.assertNotIn(self.group, self.requester.groups.all())

    def test_nonexistent_request_is_indistinguishable_from_unauthorized(self):
        # Probing ids must not reveal which requests exist (no enumeration
        # oracle) and must never leak exception text.
        self.client.force_login(_makeUser("prober"))
        missing = self.client.get(reverse("review-access-request", kwargs={"id": 9999}))
        forbidden = self.client.get(self._reviewUrl())
        self.assertEqual(missing.status_code, 200)
        self.assertNotContains(missing, "DoesNotExist")
        self.assertEqual(
            missing.templates[0].name if missing.templates else None,
            forbidden.templates[0].name if forbidden.templates else None,
        )

    def test_member_of_other_group_cannot_review(self):
        self.client.force_login(self.otherMember)
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.REQUESTED)

    def test_requester_cannot_review_own_request_even_with_permission(self):
        self.requester.user_permissions.add(_permission("approveAccessRequest"))
        self.client.force_login(self.requester)
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.REQUESTED)

    def test_group_member_can_approve_group_request(self):
        self.client.force_login(self.member)
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.APPROVED)
        self.assertEqual(self.groupRequest.reviewer, self.member)
        self.assertEqual(self.groupRequest.reason, "welcome aboard")
        self.assertIsNotNone(self.groupRequest.dateReviewed)
        self.assertIn(self.group, self.requester.groups.all())
        # requester is notified
        recipients = [address for m in mail.outbox for address in m.to]
        self.assertIn(self.requester.email, recipients)

    def test_permission_holder_can_approve_permission_request(self):
        permission = _permission("manageLinkTree")
        permRequest = AccessRequests.objects.create(
            requester=self.requester,
            permission=permission,
            justification="link duty",
            status=AccessRequests.Status.REQUESTED,
        )
        self.client.force_login(self.approver)
        self.client.post(self._reviewUrl(permRequest), {"approve": "YES", "reason": "ok"})
        permRequest.refresh_from_db()
        self.assertEqual(permRequest.status, AccessRequests.Status.APPROVED)
        self.assertIn(permission, self.requester.user_permissions.all())
        # Fresh instance so the permission cache is clean
        freshRequester = User.objects.get(id=self.requester.id)
        self.assertTrue(freshRequester.has_perm(permissions.MANAGE_LINK_TREE))

    def test_group_member_cannot_approve_permission_request(self):
        permRequest = AccessRequests.objects.create(
            requester=self.requester,
            permission=_permission("manageLinkTree"),
            justification="link duty",
            status=AccessRequests.Status.REQUESTED,
        )
        self.client.force_login(self.member)
        self.client.post(self._reviewUrl(permRequest), {"approve": "YES", "reason": "ok"})
        permRequest.refresh_from_db()
        self.assertEqual(permRequest.status, AccessRequests.Status.REQUESTED)

    def test_superuser_can_approve(self):
        self.client.force_login(self.admin)
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.APPROVED)

    def test_deny_grants_nothing_and_stamps_reason(self):
        self.client.force_login(self.member)
        self._deny()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.DENIED)
        self.assertEqual(self.groupRequest.reason, "not yet")
        self.assertNotIn(self.group, self.requester.groups.all())
        recipients = [address for m in mail.outbox for address in m.to]
        self.assertIn(self.requester.email, recipients)

    def test_already_reviewed_request_cannot_be_rereviewed(self):
        self.client.force_login(self.member)
        self._approve()
        self.groupRequest.refresh_from_db()
        firstReviewDate = self.groupRequest.dateReviewed

        self.client.force_login(self.admin)
        self._deny(reason="changed my mind")
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.APPROVED)
        self.assertEqual(self.groupRequest.reviewer, self.member)
        self.assertEqual(self.groupRequest.dateReviewed, firstReviewDate)
        self.assertIn(self.group, self.requester.groups.all())


class AccessRequestListTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Anti-ICE Campaign")
        self.requester = _makeUser("requester")
        self.member = _makeUser("member")
        self.member.groups.add(self.group)
        self.request = AccessRequests.objects.create(
            requester=self.requester,
            group=self.group,
            justification="please",
            status=AccessRequests.Status.REQUESTED,
        )

    def test_requester_sees_own_request(self):
        self.client.force_login(self.requester)
        resp = self.client.get(reverse("access-request-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Anti-ICE Campaign")

    def test_approver_sees_actionable_request(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse("access-request-list"))
        self.assertContains(resp, "Anti-ICE Campaign")
        self.assertContains(
            resp, reverse("review-access-request", kwargs={"id": self.request.id})
        )

    def test_uninvolved_user_sees_empty_state(self):
        self.client.force_login(_makeUser("bystander"))
        resp = self.client.get(reverse("access-request-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Anti-ICE Campaign")


class AdminGroupAssignmentTests(TestCase):
    def setUp(self):
        self.admin = _makeUser("staffadmin", is_staff=True, is_superuser=True)
        self.group = Group.objects.create(name="Anti-ICE Campaign")
        self.client.force_login(self.admin)

    def test_user_add_form_offers_groups_and_assigns_at_creation(self):
        resp = self.client.get("/admin/tools/user/add/")
        self.assertContains(resp, 'name="groups"')

        self.client.post(
            "/admin/tools/user/add/",
            {
                "username": "fresh",
                "password1": "s3cure-pw-123",
                "password2": "s3cure-pw-123",
                "usable_password": "true",
                "email": "fresh@example.com",
                "first_name": "Fresh",
                "last_name": "User",
                "groups": [self.group.id],
                "_save": "Save",
            },
        )
        user = User.objects.get(username="fresh")
        self.assertIn(self.group, user.groups.all())

    def test_group_form_offers_users_and_syncs_membership(self):
        target = _makeUser("target")
        resp = self.client.get(f"/admin/auth/group/{self.group.id}/change/")
        self.assertContains(resp, 'name="users"')

        # Add a member from the group side
        self.client.post(
            f"/admin/auth/group/{self.group.id}/change/",
            {"name": self.group.name, "permissions": [], "users": [target.id], "_save": "Save"},
        )
        self.assertIn(self.group, target.groups.all())

        # Remove them again
        self.client.post(
            f"/admin/auth/group/{self.group.id}/change/",
            {"name": self.group.name, "permissions": [], "users": [], "_save": "Save"},
        )
        target.refresh_from_db()
        self.assertNotIn(self.group, target.groups.all())

    def test_access_request_admin_add_disabled(self):
        resp = self.client.get("/admin/tools/accessrequests/add/")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/admin/tools/accessrequests/")
        self.assertEqual(resp.status_code, 200)


class HomeMenuTests(TestCase):
    def test_permissionless_user_sees_request_access_links_only(self):
        self.client.force_login(_makeUser("nobody"))
        resp = self.client.get("/")
        self.assertContains(resp, "Request Access")
        self.assertContains(resp, "View Access Requests")
        self.assertNotContains(resp, "Create an Event")

    def test_gated_entries_still_work(self):
        user = _makeUser("publisher")
        user.user_permissions.add(_permission("publishEvent"))
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertContains(resp, "Create an Event")
        self.assertContains(resp, "Request Access")
