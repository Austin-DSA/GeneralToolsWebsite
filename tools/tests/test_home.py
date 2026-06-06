from django.test import TestCase

from tools.tests.support import LoginClientMixin, UserFactory, fastHashing


@fastHashing
class HomeMenuTests(LoginClientMixin, TestCase):
    def test_permissionless_user_sees_request_access_links_only(self):
        # Brittle: asserts user-facing menu copy ("Request Access",
        # "View Access Requests", "Create an Event") gated by has_perm in
        # tools/views.py PAGES. Template/copy coupled. Assertion unchanged.
        self.loginAs(UserFactory.make("nobody"))
        resp = self.client.get("/")
        self.assertContains(resp, "Request Access")
        self.assertContains(resp, "View Access Requests")
        self.assertNotContains(resp, "Create an Event")

    def test_gated_entries_still_work(self):
        user = UserFactory.make("publisher", perms=("publishEvent",))
        self.loginAs(user)
        resp = self.client.get("/")
        self.assertContains(resp, "Create an Event")
        self.assertContains(resp, "Request Access")
