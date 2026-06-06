from django.test import TestCase

from tools.tests.support import LoginClientMixin, UserFactory, fastHashing


@fastHashing
class HomeDirectoryTests(LoginClientMixin, TestCase):
    """The home page is the switchboard: a directory section per domain the
    user can see into, listing every tool they can reach."""

    def test_permissionless_user_sees_access_section_only(self):
        # Access has permission=None tools, so its section always renders;
        # Events and Link Trees need perms this user lacks, so theirs (and
        # their nav dropdowns) don't.
        self.loginAs(UserFactory.make("nobody"))
        resp = self.client.get("/")
        self.assertContains(resp, "Access")
        self.assertNotContains(resp, "Events")
        self.assertNotContains(resp, "Link Trees")

    def test_gated_domains_appear_with_their_permission(self):
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/")
        self.assertContains(resp, "Events")
        # The directory lists the tools themselves right on the home page
        self.assertContains(resp, "Create an Event")
        self.assertNotContains(resp, "View Published Events")


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

    def test_superuser_does_not_see_my_access(self):
        # Superusers implicitly hold every permission - My Access is noise
        self.loginAs(UserFactory.superuser("root"))
        resp = self.client.get("/access")
        self.assertNotContains(resp, "My Access")
        self.assertContains(resp, "Manage Member Access")

    def test_unknown_domain_404s(self):
        # Since the bounded domain route replaced the <slug:domainSlug>
        # catch-all, this 404 comes from the resolver (no route matches) rather
        # than from views.domain raising Http404 - same observable result.
        self.loginAs(UserFactory.make("nobody"))
        self.assertEqual(self.client.get("/no-such-domain").status_code, 404)

    def test_requires_login(self):
        resp = self.client.get("/events")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])
