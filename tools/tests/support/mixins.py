from django.contrib.auth.models import Group

from .factories import UserFactory, permission


class AccessFixtureMixin:
    """The recurring access-feature cast: an approver-admin, a plain member, and
    the 'Anti-ICE Campaign' group. Opt-in — suites that don't need users (Link
    Tree) simply don't mix this in (Interface Segregation).

    Provides a single buildCast() builder rather than a fat setUp, so a suite can
    call it from setUp OR setUpTestData (see plan §4) and pick its own DB-build
    cadence."""

    GROUP_NAME = "Anti-ICE Campaign"

    @classmethod
    def buildCast(cls):
        group = Group.objects.create(name=cls.GROUP_NAME)
        member = UserFactory.make("member", groups=[group])
        admin = UserFactory.admin("admin")
        requester = UserFactory.make("requester")
        return {"group": group, "member": member, "admin": admin, "requester": requester}


class MailAssertionsMixin:
    """Recipient-list helpers over django.core.mail.outbox (the flatten idiom
    repeated ~8x in tests.py)."""

    def allRecipients(self):
        from django.core import mail
        return [address for message in mail.outbox for address in message.to]

    def assertEmailedTo(self, email, times=None):
        recipients = self.allRecipients()
        if times is None:
            self.assertIn(email, recipients)
        else:
            self.assertEqual(recipients.count(email), times,
                             f"expected {times} email(s) to {email}, got {recipients.count(email)}")

    def assertNotEmailedTo(self, email):
        self.assertNotIn(email, self.allRecipients())


class LoginClientMixin:
    """A loginAs() that returns the user, so a test reads as one line:
        requester = self.loginAs(self.requester)
    Replaces the force_login-then-post boilerplate."""

    def loginAs(self, user):
        self.client.force_login(user)
        return user
