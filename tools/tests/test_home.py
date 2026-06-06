from django.test import TestCase

from tools.tests.support import LoginClientMixin, UserFactory, fastHashing


@fastHashing
class HomeDomainCardTests(LoginClientMixin, TestCase):
    """The home page shows one card per domain the user can see into; tool
    titles live on the domain landing pages, not here."""

    def test_permissionless_user_sees_access_card_only(self):
        # Access has permission=None tools, so its card always renders; Events
        # and Link Trees need perms this user lacks, so theirs (and their nav
        # links) don't.
        self.loginAs(UserFactory.make("nobody"))
        resp = self.client.get("/")
        self.assertContains(resp, "Access")
        self.assertNotContains(resp, "Events")
        self.assertNotContains(resp, "Link Trees")

    def test_gated_domains_appear_with_their_permission(self):
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/")
        self.assertContains(resp, "Events")
        # Tool titles moved off the home page onto the domain landing pages
        self.assertNotContains(resp, "Create an Event")


@fastHashing
class DomainPageTests(LoginClientMixin, TestCase):
    def test_lists_only_permitted_tools(self):
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/events")
        self.assertContains(resp, "Create an Event")
        self.assertNotContains(resp, "View Published Events")

    def test_domain_without_visible_tools_offers_request_access(self):
        self.loginAs(UserFactory.make("nobody"))
        resp = self.client.get("/events")
        self.assertContains(resp, "Request access")

    def test_unknown_domain_404s(self):
        self.loginAs(UserFactory.make("nobody"))
        self.assertEqual(self.client.get("/no-such-domain").status_code, 404)

    def test_requires_login(self):
        resp = self.client.get("/events")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])
