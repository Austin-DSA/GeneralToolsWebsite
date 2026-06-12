from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from tools.models import AccessRequests

from tools.tests.support import (
    LoginClientMixin, MailAssertionsMixin, UserFactory, fastHashing, permission, refetchForPerms,
)


@fastHashing
class ManageGroupsTests(MailAssertionsMixin, LoginClientMixin, TestCase):
    def setUp(self):
        # Perm-admin + a member who is NOT in the group (tests add membership),
        # so this builds directly rather than via AccessFixtureMixin.buildCast().
        self.group = Group.objects.create(name="Anti-ICE Campaign")
        self.admin = UserFactory.admin("admin")
        self.member = UserFactory.make("member")

    def test_plain_user_cannot_open_group_pages(self):
        self.loginAs(self.member)
        resp = self.client.get(reverse("manage-groups"))
        self.assertEqual(resp.status_code, 302)  # bounced by permission_required
        resp = self.client.post(
            reverse("manage-group", kwargs={"groupId": self.group.id}),
            {"name": "Hijacked", "permissions": []},
        )
        self.assertEqual(resp.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Anti-ICE Campaign")
        resp = self.client.post(
            reverse("manage-group-delete", kwargs={"groupId": self.group.id}),
            {"confirmName": self.group.name},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Group.objects.filter(id=self.group.id).exists())

    def test_admin_sees_group_list(self):
        self.loginAs(self.admin)
        resp = self.client.get(reverse("manage-groups"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Anti-ICE Campaign")
        self.assertContains(resp, reverse("manage-group", kwargs={"groupId": self.group.id}))

    def test_create_group(self):
        self.loginAs(self.admin)
        resp = self.client.post(reverse("manage-groups"), {"name": "Mutual Aid"})
        created = Group.objects.get(name="Mutual Aid")
        self.assertRedirects(resp, reverse("manage-group", kwargs={"groupId": created.id}))

    def test_create_duplicate_name_rejected(self):
        self.loginAs(self.admin)
        resp = self.client.post(reverse("manage-groups"), {"name": "anti-ice campaign"})
        self.assertEqual(resp.status_code, 200)  # re-rendered with the error
        self.assertContains(resp, "already exists")
        self.assertEqual(Group.objects.count(), 1)

    def test_edit_name_permissions_and_members(self):
        perm = permission("manageLinkTree")
        self.loginAs(self.admin)
        resp = self.client.post(
            reverse("manage-group", kwargs={"groupId": self.group.id}),
            {"name": "Anti-ICE Organizers", "permissions": [perm.id], "addMembers": [self.member.id]},
        )
        self.assertEqual(resp.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Anti-ICE Organizers")
        self.assertIn(perm, self.group.permissions.all())
        self.assertIn(self.member, self.group.user_set.all())
        # the member now effectively holds the permission via the group
        self.member = refetchForPerms(self.member)  # fresh perm cache
        self.assertTrue(self.member.has_perm("tools.manageLinkTree"))

    def test_non_custom_group_permissions_preserved(self):
        # A model permission attached in /admin/ must survive a save here
        otherPermission = Permission.objects.get(codename="view_linktree")
        self.group.permissions.add(otherPermission)
        self.loginAs(self.admin)
        self.client.post(
            reverse("manage-group", kwargs={"groupId": self.group.id}),
            {"name": self.group.name, "permissions": []},
        )
        self.assertIn(otherPermission, self.group.permissions.all())

    def test_adding_member_closes_their_pending_request(self):
        pending = AccessRequests.objects.create(
            requester=self.member, group=self.group, justification="please"
        )
        bystander = UserFactory.make("bystander")
        bystanderPending = AccessRequests.objects.create(
            requester=bystander, group=self.group, justification="me too"
        )
        self.loginAs(self.admin)
        self.client.post(
            reverse("manage-group", kwargs={"groupId": self.group.id}),
            {"name": self.group.name, "permissions": [], "addMembers": [self.member.id]},
        )
        pending.refresh_from_db()
        self.assertEqual(pending.status, AccessRequests.Status.APPROVED)
        self.assertEqual(pending.reviewer, self.admin)
        self.assertEqual(pending.reason, "Access granted directly")
        self.assertEmailedTo(self.member.email)
        # the bystander wasn't added, so their request stays pending
        bystanderPending.refresh_from_db()
        self.assertEqual(bystanderPending.status, AccessRequests.Status.REQUESTED)

    def test_remove_member(self):
        self.member.groups.add(self.group)
        self.loginAs(self.admin)
        self.client.post(
            reverse("manage-group", kwargs={"groupId": self.group.id}),
            {"name": self.group.name, "permissions": [], "removeMembers": [self.member.id]},
        )
        self.assertNotIn(self.group, self.member.groups.all())

    def test_add_remove_overlap_rejected(self):
        self.loginAs(self.admin)
        resp = self.client.post(
            reverse("manage-group", kwargs={"groupId": self.group.id}),
            {
                "name": self.group.name,
                "permissions": [],
                "addMembers": [self.member.id],
                "removeMembers": [self.member.id],
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "both added and removed")
        self.assertNotIn(self.group, self.member.groups.all())

    def test_member_search_endpoint(self):
        outsider = UserFactory.make("rosalind")
        insider = UserFactory.make("rosamund")
        insider.groups.add(self.group)
        url = reverse("manage-group-member-search", kwargs={"groupId": self.group.id})
        self.loginAs(self.admin)

        resp = self.client.get(url, {"q": "rosa"})
        usernames = [match["username"] for match in resp.json()["results"]]
        self.assertIn(outsider.username, usernames)
        self.assertNotIn(insider.username, usernames)  # already a member

        # short/empty queries return nothing rather than dumping the org
        resp = self.client.get(url, {"q": "r"})
        self.assertEqual(resp.json()["results"], [])

        # gated like the rest of the manage pages
        self.loginAs(self.member)
        resp = self.client.get(url, {"q": "rosa"})
        self.assertEqual(resp.status_code, 302)

    def test_delete_requires_exact_name(self):
        self.loginAs(self.admin)
        resp = self.client.post(
            reverse("manage-group-delete", kwargs={"groupId": self.group.id}),
            {"confirmName": "wrong name"},
        )
        self.assertRedirects(resp, reverse("manage-group", kwargs={"groupId": self.group.id}))
        self.assertTrue(Group.objects.filter(id=self.group.id).exists())

    def test_delete_denies_pending_requests_and_removes_group(self):
        self.member.groups.add(self.group)
        pending = AccessRequests.objects.create(
            requester=UserFactory.make("hopeful"), group=self.group, justification="please"
        )
        self.loginAs(self.admin)
        resp = self.client.post(
            reverse("manage-group-delete", kwargs={"groupId": self.group.id}),
            {"confirmName": "Anti-ICE Campaign"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Group.objects.filter(id=self.group.id).exists())
        self.member.refresh_from_db()
        self.assertEqual(self.member.groups.count(), 0)
        # the stranded request was denied with an explanation, not deleted
        pending.refresh_from_db()
        self.assertEqual(pending.status, AccessRequests.Status.DENIED)
        self.assertIn("was deleted", pending.reason)
        self.assertIsNone(pending.group)  # SET_NULL keeps the audit record
        self.assertEmailedTo("hopeful@example.com")
