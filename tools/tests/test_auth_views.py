import re

from django.core import mail
from django.test import TestCase

from tools.tests.support import UserFactory


class AuthViewTests(TestCase):
    def test_login_page_renders(self):
        resp = self.client.get("/accounts/login/")
        self.assertEqual(resp.status_code, 200)

    def test_login_without_next_redirects_home(self):
        UserFactory.make("loginuser")
        resp = self.client.post(
            "/accounts/login/", {"username": "loginuser", "password": "s3cure-pw-123"}
        )
        self.assertRedirects(resp, "/")

    def test_password_reset_flow(self):
        UserFactory.make("forgetful", email="forgetful@example.com")
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

        self.client.force_login(UserFactory.make("changer"))
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
        self.client.force_login(UserFactory.make("leaver"))
        resp = self.client.get("/accounts/logout/")
        self.assertEqual(resp.status_code, 405)  # Django 5: GET logout removed
        resp = self.client.post("/accounts/logout/")
        self.assertEqual(resp.status_code, 200)
