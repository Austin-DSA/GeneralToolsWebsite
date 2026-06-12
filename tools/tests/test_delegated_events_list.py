"""Viewer-aware link routing on the delegated-events list.

A user who can see the list (viewDelegatedEventList) does not necessarily hold
approveDelegatedEvent, and even an approver may not be an authorizer for a
given row's owner. getUrlFor(user) must send only users who can actually act
to the approve page - everyone else gets the read-only detail page instead of
a dead-end rejection at the approve view's gates.
"""
import datetime

from django.test import TestCase
from django.urls import reverse

from tools.models import DelegatedEvents, EventOwners
from tools.tests.support import LoginClientMixin, UserFactory, fastHashing

EVENT_MOMENT = datetime.datetime(2030, 1, 1, 18, 0, tzinfo=datetime.UTC)


def _makeOwner(name="Test Committee") -> EventOwners:
    return EventOwners.objects.create(name=name, expiration=EVENT_MOMENT, isPermanent=True)


def _makeDelegatedEvent(owner, status=DelegatedEvents.Status.REQUESTED) -> DelegatedEvents:
    return DelegatedEvents.objects.create(
        title="Delegated Test Event", start=EVENT_MOMENT, end=EVENT_MOMENT,
        timezone="America/Chicago",
        locationName="", streetAddress="", city="", state="", zip="", country="",
        description="", instructions="",
        dateCreated=EVENT_MOMENT,
        owner=owner, status=status, reason="",
    )


def _detailHref(event) -> str:
    return f'href="{reverse("delegated-event-detail", kwargs={"pk": event.id})}"'


def _approveHref(event) -> str:
    return f'href="{reverse("approve-delegated-event", kwargs={"id": event.id})}"'


@fastHashing
class DelegatedEventListLinkRoutingTests(LoginClientMixin, TestCase):

    def test_viewer_without_approve_perm_gets_detail_link_on_requested_row(self):
        event = _makeDelegatedEvent(_makeOwner())
        self.loginAs(UserFactory.make("viewer", perms=("viewDelegatedEventList",)))
        resp = self.client.get(reverse("delegated-event-list"))
        self.assertContains(resp, _detailHref(event))
        self.assertNotContains(resp, _approveHref(event))

    def test_viewers_detail_link_actually_opens(self):
        # The destination the viewer is routed to must not itself reject them.
        event = _makeDelegatedEvent(_makeOwner())
        self.loginAs(UserFactory.make("viewer", perms=("viewDelegatedEventList",)))
        resp = self.client.get(reverse("delegated-event-detail", kwargs={"pk": event.id}))
        self.assertEqual(resp.status_code, 200)

    def test_authorizer_with_approve_perm_gets_approve_link_on_requested_row(self):
        owner = _makeOwner()
        event = _makeDelegatedEvent(owner)
        approver = UserFactory.make(
            "approver", perms=("viewDelegatedEventList", "approveDelegatedEvent"))
        owner.authorizers.add(approver)
        self.loginAs(approver)
        resp = self.client.get(reverse("delegated-event-list"))
        self.assertContains(resp, _approveHref(event))

    def test_cross_owner_approver_gets_detail_link(self):
        # Holding approveDelegatedEvent is not enough - the approve view also
        # requires membership in this owner's authorizers.
        event = _makeDelegatedEvent(_makeOwner())
        self.loginAs(UserFactory.make(
            "outsideApprover", perms=("viewDelegatedEventList", "approveDelegatedEvent")))
        resp = self.client.get(reverse("delegated-event-list"))
        self.assertContains(resp, _detailHref(event))
        self.assertNotContains(resp, _approveHref(event))

    def test_decided_row_links_to_detail_even_for_authorizer(self):
        owner = _makeOwner()
        event = _makeDelegatedEvent(owner, status=DelegatedEvents.Status.APPROVED)
        approver = UserFactory.make(
            "approver", perms=("viewDelegatedEventList", "approveDelegatedEvent"))
        owner.authorizers.add(approver)
        self.loginAs(approver)
        resp = self.client.get(reverse("delegated-event-list"))
        self.assertContains(resp, _detailHref(event))
        self.assertNotContains(resp, _approveHref(event))

    def test_ownerless_row_routes_to_detail_without_error(self):
        # owner is SET_NULL - a row can outlive its EventOwners.
        event = _makeDelegatedEvent(owner=None)
        self.loginAs(UserFactory.make(
            "approver", perms=("viewDelegatedEventList", "approveDelegatedEvent")))
        resp = self.client.get(reverse("delegated-event-list"))
        self.assertContains(resp, _detailHref(event))
