from django.contrib.auth.models import Group
from django.test import TestCase

from tools.models import User

from tools.tests.support import UserFactory, fastHashing


# Admin add form POSTs a real password, but the assertions are about group
# assignment, not hash verification - safe to use the fast hasher.
@fastHashing
class AdminGroupAssignmentTests(TestCase):
    def setUp(self):
        self.admin = UserFactory.make("staffadmin", is_staff=True, is_superuser=True)
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
        target = UserFactory.make("target")
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
