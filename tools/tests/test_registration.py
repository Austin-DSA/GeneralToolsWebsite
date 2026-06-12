from django.test import TestCase
from django.urls import reverse

from tools.models import User

from tools.tests.support import UserFactory, fastHashing


# --- auth & access requests --------------------------------------------------
#
# The Django test runner swaps the email backend to locmem (assert via
# django.core.mail.outbox) and allows the 'testserver' host automatically.


# @fastHashing swaps to MD5 only for speed; password VALIDATORS are independent
# of PASSWORD_HASHERS, so test_register_enforces_password_validators still
# rejects the weak password.
@fastHashing
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
        UserFactory.make("newcomer")
        resp = self.client.post(reverse("register"), self._validData())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(username="newcomer").count(), 1)

    def test_register_rejects_duplicate_email_case_insensitive(self):
        UserFactory.make("existing", email="newcomer@example.com")
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
        self.client.force_login(UserFactory.make("alreadyhere"))
        resp = self.client.get(reverse("register"))
        self.assertRedirects(resp, "/")
