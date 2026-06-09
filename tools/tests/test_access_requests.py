import datetime
from unittest import mock

from django.contrib.auth.models import Group
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from tools import permissions
from tools.models import AccessRequests, EventOwners

from tools.tests.support import (
    AccessFixtureMixin, LoginClientMixin, MailAssertionsMixin,
    UserFactory, fastHashing, permission, refetchForPerms,
)

# isActive() short-circuits on isPermanent, but expiration is a required column.
FAR_FUTURE = datetime.datetime(2099, 12, 31, tzinfo=datetime.UTC)


@fastHashing
class AccessRequestCreateTests(AccessFixtureMixin, MailAssertionsMixin, LoginClientMixin, TestCase):
    def setUp(self):
        cast = self.buildCast()
        self.group, self.member = cast["group"], cast["member"]
        self.admin, self.requester = cast["admin"], cast["requester"]
        # buildCast's admin holds approveAccessRequest; the original suite's
        # admin was a superuser - keep both so the cast matches the old fixture.
        self.admin.is_superuser = True
        self.admin.save()
        self.approver = UserFactory.make("approver", perms=("approveAccessRequest",))
        # Members self-request to JOIN an event owner (committee); approval adds
        # them to its authorizers, and the owner's current authorizers are the
        # peer reviewers (this replaced self-requesting Django groups).
        self.owner = EventOwners.objects.create(
            name="Political Education", isPermanent=True, expiration=FAR_FUTURE,
        )
        self.ownerMember = UserFactory.make("ownermember")
        self.owner.authorizers.add(self.ownerMember)

    def _post(self, target, justification="I work on this campaign."):
        return self.client.post(
            reverse("request-access"),
            {"target": target, "justification": justification},
        )

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse("request-access"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_owner_request_creates_row(self):
        self.loginAs(self.requester)
        resp = self._post(f"o:{self.owner.id}")
        self.assertEqual(resp.status_code, 200)
        request = AccessRequests.objects.get()
        self.assertEqual(request.status, AccessRequests.Status.REQUESTED)
        self.assertEqual(request.requester, self.requester)
        self.assertEqual(request.owner, self.owner)
        self.assertIsNone(request.group)
        self.assertIsNone(request.permission)
        self.assertEqual(request.justification, "I work on this campaign.")
        self.assertIsNotNone(request.dateCreated)
        self.assertIsNone(request.dateReviewed)

    def test_owner_request_emails_admins_approvers_and_authorizers_once_each(self):
        # admin is also an authorizer - must still get exactly one email
        self.owner.authorizers.add(self.admin)
        self.loginAs(self.requester)
        self._post(f"o:{self.owner.id}")

        self.assertEmailedTo(self.admin.email, times=1)
        self.assertEmailedTo(self.ownerMember.email, times=1)
        self.assertEmailedTo(self.approver.email, times=1)
        # requester gets a confirmation
        self.assertEmailedTo(self.requester.email, times=1)

        request = AccessRequests.objects.get()
        reviewPath = reverse("review-access-request", kwargs={"id": request.id})
        approverMails = [m for m in mail.outbox if self.ownerMember.email in m.to]
        self.assertIn(reviewPath, approverMails[0].body)

    def test_permission_request_does_not_email_owner_authorizers(self):
        self.loginAs(self.requester)
        perm = permission("manageLinkTree")
        self._post(f"p:{perm.id}")

        self.assertNotEmailedTo(self.ownerMember.email)
        self.assertEmailedTo(self.admin.email)
        self.assertEmailedTo(self.approver.email)

        request = AccessRequests.objects.get()
        self.assertEqual(request.permission, perm)
        self.assertIsNone(request.owner)
        self.assertIsNone(request.group)

    def test_existing_authorizer_cannot_request_their_owner(self):
        self.loginAs(self.ownerMember)
        resp = self._post(f"o:{self.owner.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccessRequests.objects.count(), 0)

    def test_groups_are_no_longer_self_requestable(self):
        # EventOwners replaced Django groups on this form; a stale/crafted POST
        # naming a group is rejected as an invalid choice, creating nothing.
        self.loginAs(self.requester)
        resp = self._post(f"g:{self.group.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccessRequests.objects.count(), 0)

    def test_duplicate_pending_request_is_rejected(self):
        self.loginAs(self.requester)
        self._post(f"o:{self.owner.id}")
        resp = self._post(f"o:{self.owner.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccessRequests.objects.count(), 1)

    def test_email_failure_does_not_fail_request_creation(self):
        self.loginAs(self.requester)
        with mock.patch("tools.accessViews.send_mail", side_effect=Exception("smtp down")):
            resp = self._post(f"o:{self.owner.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AccessRequests.objects.count(), 1)


@fastHashing
class AccessRequestReviewTests(AccessFixtureMixin, MailAssertionsMixin, LoginClientMixin, TestCase):
    def setUp(self):
        cast = self.buildCast()
        self.group, self.member = cast["group"], cast["member"]
        self.admin, self.requester = cast["admin"], cast["requester"]
        # buildCast's admin holds approveAccessRequest; the original suite's
        # admin was a superuser - keep both so the cast matches the old fixture.
        self.admin.is_superuser = True
        self.admin.save()
        # extras this suite needs on top of the standard cast
        self.otherGroup = Group.objects.create(name="Other Campaign")
        self.otherMember = UserFactory.make("othermember", groups=[self.otherGroup])
        self.approver = UserFactory.make("approver", perms=("approveAccessRequest",))
        self.groupRequest = AccessRequests.objects.create(
            requester=self.requester,
            group=self.group,
            justification="please",
            status=AccessRequests.Status.REQUESTED,
        )

    def _reviewUrl(self, request=None):
        return reverse(
            "review-access-request", kwargs={"id": (request or self.groupRequest).id}
        )

    def _approve(self, reason="welcome aboard"):
        return self.client.post(self._reviewUrl(), {"approve": "YES", "reason": reason})

    def _deny(self, reason="not yet"):
        return self.client.post(self._reviewUrl(), {"approve": "NO", "reason": reason})

    def test_random_user_cannot_review(self):
        self.loginAs(UserFactory.make("random"))
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.REQUESTED)
        self.assertNotIn(self.group, self.requester.groups.all())

    def test_nonexistent_request_is_indistinguishable_from_unauthorized(self):
        # Brittle by design: this asserts the anti-enumeration CONTRACT - a
        # missing id and a forbidden id must render the SAME template so ids
        # can't be probed. It is not coupled to template internals; keep it.
        # Probing ids must not reveal which requests exist (no enumeration
        # oracle) and must never leak exception text.
        self.loginAs(UserFactory.make("prober"))
        missing = self.client.get(reverse("review-access-request", kwargs={"id": 9999}))
        forbidden = self.client.get(self._reviewUrl())
        self.assertEqual(missing.status_code, 200)
        self.assertNotContains(missing, "DoesNotExist")
        self.assertEqual(
            missing.templates[0].name if missing.templates else None,
            forbidden.templates[0].name if forbidden.templates else None,
        )

    def test_member_of_other_group_cannot_review(self):
        self.loginAs(self.otherMember)
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.REQUESTED)

    def test_requester_cannot_review_own_request_even_with_permission(self):
        self.requester.user_permissions.add(permission("approveAccessRequest"))
        self.loginAs(self.requester)
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.REQUESTED)

    def test_group_member_can_approve_group_request(self):
        self.loginAs(self.member)
        self._approve()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.APPROVED)
        self.assertEqual(self.groupRequest.reviewer, self.member)
        self.assertEqual(self.groupRequest.reason, "welcome aboard")
        self.assertIsNotNone(self.groupRequest.dateReviewed)
        self.assertIn(self.group, self.requester.groups.all())
        # requester is notified
        self.assertEmailedTo(self.requester.email)

    def test_owner_authorizer_can_approve_owner_request(self):
        owner = EventOwners.objects.create(
            name="Political Education", isPermanent=True, expiration=FAR_FUTURE,
        )
        authorizer = UserFactory.make("authorizer")
        owner.authorizers.add(authorizer)
        ownerRequest = AccessRequests.objects.create(
            requester=self.requester, owner=owner,
            justification="want in", status=AccessRequests.Status.REQUESTED,
        )
        self.loginAs(authorizer)
        self.client.post(self._reviewUrl(ownerRequest), {"approve": "YES", "reason": "welcome"})
        ownerRequest.refresh_from_db()
        self.assertEqual(ownerRequest.status, AccessRequests.Status.APPROVED)
        # Approval adds the requester to the owner's authorizers...
        self.assertIn(self.requester, owner.authorizers.all())
        # ...and adds them to the Event Publishers role group, which carries the
        # publish permission - so the join is actually useful (authorizer
        # membership alone is inert without the page-level permission).
        self.assertTrue(self.requester.groups.filter(name="Event Publishers").exists())
        self.assertTrue(refetchForPerms(self.requester).has_perm(permissions.PUBLISH_EVENT))
        self.assertEmailedTo(self.requester.email)

    def test_non_authorizer_cannot_approve_owner_request(self):
        owner = EventOwners.objects.create(
            name="Political Education", isPermanent=True, expiration=FAR_FUTURE,
        )
        owner.authorizers.add(UserFactory.make("someauthorizer"))
        ownerRequest = AccessRequests.objects.create(
            requester=self.requester, owner=owner,
            justification="want in", status=AccessRequests.Status.REQUESTED,
        )
        # self.member is a group member but NOT an authorizer of this owner
        self.loginAs(self.member)
        self.client.post(self._reviewUrl(ownerRequest), {"approve": "YES", "reason": "ok"})
        ownerRequest.refresh_from_db()
        self.assertEqual(ownerRequest.status, AccessRequests.Status.REQUESTED)
        self.assertNotIn(self.requester, owner.authorizers.all())

    def test_permission_holder_can_approve_permission_request(self):
        perm = permission("manageLinkTree")
        permRequest = AccessRequests.objects.create(
            requester=self.requester,
            permission=perm,
            justification="link duty",
            status=AccessRequests.Status.REQUESTED,
        )
        self.loginAs(self.approver)
        self.client.post(self._reviewUrl(permRequest), {"approve": "YES", "reason": "ok"})
        permRequest.refresh_from_db()
        self.assertEqual(permRequest.status, AccessRequests.Status.APPROVED)
        self.assertIn(perm, self.requester.user_permissions.all())
        # Fresh instance so the permission cache is clean
        freshRequester = refetchForPerms(self.requester)
        self.assertTrue(freshRequester.has_perm(permissions.MANAGE_LINK_TREE))

    def test_group_member_cannot_approve_permission_request(self):
        permRequest = AccessRequests.objects.create(
            requester=self.requester,
            permission=permission("manageLinkTree"),
            justification="link duty",
            status=AccessRequests.Status.REQUESTED,
        )
        self.loginAs(self.member)
        self.client.post(self._reviewUrl(permRequest), {"approve": "YES", "reason": "ok"})
        permRequest.refresh_from_db()
        self.assertEqual(permRequest.status, AccessRequests.Status.REQUESTED)

    def test_superuser_can_approve_without_reason(self):
        # reason is optional - an approval with no note must go through
        self.loginAs(self.admin)
        self.client.post(self._reviewUrl(), {"approve": "YES", "reason": ""})
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.APPROVED)
        self.assertEqual(self.groupRequest.reason, "")

    def test_deny_grants_nothing_and_stamps_reason(self):
        self.loginAs(self.member)
        self._deny()
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.DENIED)
        self.assertEqual(self.groupRequest.reason, "not yet")
        self.assertNotIn(self.group, self.requester.groups.all())
        self.assertEmailedTo(self.requester.email)

    def test_already_reviewed_request_cannot_be_rereviewed(self):
        self.loginAs(self.member)
        self._approve()
        self.groupRequest.refresh_from_db()
        firstReviewDate = self.groupRequest.dateReviewed

        self.loginAs(self.admin)
        self._deny(reason="changed my mind")
        self.groupRequest.refresh_from_db()
        self.assertEqual(self.groupRequest.status, AccessRequests.Status.APPROVED)
        self.assertEqual(self.groupRequest.reviewer, self.member)
        self.assertEqual(self.groupRequest.dateReviewed, firstReviewDate)
        self.assertIn(self.group, self.requester.groups.all())


@fastHashing
class AccessRequestListTests(AccessFixtureMixin, LoginClientMixin, TestCase):
    def setUp(self):
        cast = self.buildCast()
        self.group, self.member = cast["group"], cast["member"]
        self.requester = cast["requester"]
        self.request = AccessRequests.objects.create(
            requester=self.requester,
            group=self.group,
            justification="please",
            status=AccessRequests.Status.REQUESTED,
        )

    def test_requester_sees_own_request(self):
        self.loginAs(self.requester)
        resp = self.client.get(reverse("access-request-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Anti-ICE Campaign")

    def test_approver_sees_actionable_request(self):
        self.loginAs(self.member)
        resp = self.client.get(reverse("access-request-list"))
        self.assertContains(resp, "Anti-ICE Campaign")
        self.assertContains(
            resp, reverse("review-access-request", kwargs={"id": self.request.id})
        )

    def test_uninvolved_user_sees_empty_state(self):
        self.loginAs(UserFactory.make("bystander"))
        resp = self.client.get(reverse("access-request-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Anti-ICE Campaign")

    def test_dates_render_in_browser_timezone(self):
        # Brittle (tz/middleware): depends on TimezoneMiddleware activating the
        # zone from the django_timezone cookie (set by base.html), so timestamps
        # show local time instead of UTC. Asserting CST/CDT literals. Keep as-is.
        # TimezoneMiddleware activates the tz from the django_timezone cookie
        # (set by base.html), so timestamps show local time instead of UTC.
        self.client.cookies["django_timezone"] = "America/Chicago"
        self.loginAs(self.member)
        resp = self.client.get(reverse("access-request-list"))
        self.assertTrue(
            b"CST" in resp.content or b"CDT" in resp.content,
            "expected Central-time timestamp on the page",
        )
        self.assertNotContains(resp, "UTC")
