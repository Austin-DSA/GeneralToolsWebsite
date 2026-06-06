from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from tools.tests.support import LoginClientMixin, UserFactory, fastHashing, permission


@fastHashing
class MyAccessTests(LoginClientMixin, TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Anti-ICE Campaign")
        self.group.permissions.add(permission("manageLinkTree"))

    def test_requires_login(self):
        resp = self.client.get(reverse("my-access"))
        self.assertEqual(resp.status_code, 302)

    def test_shows_groups_and_permission_sources(self):
        # Brittle: asserts the exact human-readable permission label strings.
        # Source of truth for these labels is tools/permissions.py — if a label
        # is reworded there, update the literals here. Assertion unchanged.
        member = UserFactory.make("member", groups=[self.group], perms=("publishEvent",))
        self.loginAs(member)
        resp = self.client.get(reverse("my-access"))
        self.assertContains(resp, "Anti-ICE Campaign")
        # group-derived permission, annotated with its source group
        self.assertContains(resp, "Allowed to manage link trees, items, and QR codes")
        self.assertContains(resp, "Via Anti-ICE Campaign")
        # directly-granted permission
        self.assertContains(resp, "Allowed to publish events")
        self.assertContains(resp, "Granted directly")

    def test_empty_state_for_fresh_account(self):
        # Brittle: asserts user-facing copy ("not in any groups" / "don't have
        # any permissions") rendered by the my-access template. Template-wording
        # coupled. Assertion unchanged.
        self.loginAs(UserFactory.make("fresh"))
        resp = self.client.get(reverse("my-access"))
        self.assertContains(resp, "not in any groups")
        self.assertContains(resp, "don't have any permissions")
