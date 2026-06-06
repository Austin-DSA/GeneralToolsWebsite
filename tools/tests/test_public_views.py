from django.test import TestCase
from django.urls import reverse

from tools.models import LinkEvent, LinkTree, LinkTreeItem, QRCode


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
        # Brittle: asserts the literal CSS class "lt-header" (link_tree.html)
        # distinguishes a section header from a tracked /go/ link. If the
        # template renames that class this must move with it. Assertion unchanged.
        LinkTreeItem.objects.create(
            tree=self.public, order=1, kind=LinkTreeItem.Kind.SECTION_HEADER,
            label="Resolutions",
        )
        resp = self.client.get(reverse("link-tree", kwargs={"slug": "links"}))
        self.assertEqual(resp.status_code, 200)
        # The header text shows, but it is NOT a tracked /go/ link.
        self.assertContains(resp, "Resolutions")
        self.assertContains(resp, "lt-header")
