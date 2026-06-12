"""Manage Event Owners: the owner list/create/edit pages, the authorizer
roster, the stuck-request cancel action, and the isActive() enforcement that
backs them in both publish flows.

External services never run here: publishEvent is patched in the flow tests
(the views are expected to short-circuit before reaching it), and
EmailApi.sendEmailFromWebsiteAccount is patched wherever a view would send.
"""
import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from tools.forms import EventOwnerForm, NewEventForm
from tools.models import DelegatedEvents, EventOwners, PostedEvents

from tools.tests.support import LoginClientMixin, UserFactory, fastHashing


FUTURE = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
PAST = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)


def makeOwner(name, isPermanent=False, expiration=FUTURE, authorizers=()):
    owner = EventOwners.objects.create(name=name, isPermanent=isPermanent, expiration=expiration)
    for authorizer in authorizers:
        owner.authorizers.add(authorizer)
    return owner


def makeDelegatedRequest(owner, creator, title="Tabling at the farmers market"):
    return DelegatedEvents.objects.create(
        title=title,
        start=datetime.datetime(2030, 6, 1, 18, 0, tzinfo=datetime.UTC),
        end=datetime.datetime(2030, 6, 1, 19, 0, tzinfo=datetime.UTC),
        timezone="America/Chicago",
        locationName="",
        streetAddress="",
        city="Austin",
        state="TX",
        zip="",
        country="US",
        description="A table, some flyers",
        instructions="Look for the red banner",
        dateCreated=datetime.datetime.now(datetime.UTC),
        creator=creator,
        owner=owner,
        status=DelegatedEvents.Status.REQUESTED,
    )


def eventFormData(ownerName):
    """A valid NewEventForm POST (virtual event, so location fields don't apply)."""
    return {
        "owner": ownerName,
        "title": "Reading Group",
        "description": "Chapter reading group",
        "eventType": "1",  # VIRTUAL
        "timezone": "US/Central",  # the form's choices are US/* aliases, not IANA names
        "startTime": "2030-07-01T18:00",
        "endTime": "2030-07-01T19:00",
        "instructions": "Zoom link to follow",
        "city": "Austin",
        "state": "TX",
        "country": "US",
    }


@fastHashing
class ManageEventOwnersTests(LoginClientMixin, TestCase):
    def setUp(self):
        self.manager = UserFactory.make("mgr", perms=("manageEventOwners",))
        self.member = UserFactory.make("member", first_name="Rosa", last_name="Luxemburg")

    # --- access control -----------------------------------------------------

    def test_anonymous_user_redirected(self):
        resp = self.client.get(reverse("manage-event-owners"))
        self.assertEqual(resp.status_code, 302)

    def test_plain_user_cannot_access_owner_pages(self):
        owner = makeOwner("Education Committee", authorizers=[self.member])
        self.loginAs(self.member)
        resp = self.client.get(reverse("manage-event-owners"))
        self.assertEqual(resp.status_code, 302)  # bounced by permission_required
        resp = self.client.post(
            reverse("manage-event-owner", kwargs={"ownerId": owner.id}),
            {"ownerName": "Hijacked", "ownerIsPermanent": "on"},
        )
        self.assertEqual(resp.status_code, 302)
        owner.refresh_from_db()
        self.assertEqual(owner.name, "Education Committee")

    # --- list page ----------------------------------------------------------

    def test_owner_manager_sees_list(self):
        makeOwner("Education Committee", authorizers=[self.member])
        self.loginAs(self.manager)
        resp = self.client.get(reverse("manage-event-owners"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Education Committee")

    def test_list_shows_expired_badge(self):
        makeOwner("Old Working Group", expiration=PAST, authorizers=[self.member])
        self.loginAs(self.manager)
        resp = self.client.get(reverse("manage-event-owners"))
        self.assertContains(resp, "badge-expired")
        self.assertContains(resp, "Expired")

    def test_list_shows_no_authorizers_warning(self):
        makeOwner("Orphan Committee")
        self.loginAs(self.manager)
        resp = self.client.get(reverse("manage-event-owners"))
        self.assertContains(resp, "No authorizers")

    def test_list_shows_stuck_requests_card(self):
        orphanOwner = makeOwner("Orphan Committee")
        makeDelegatedRequest(orphanOwner, self.member, title="Stuck March")
        self.loginAs(self.manager)
        resp = self.client.get(reverse("manage-event-owners"))
        self.assertContains(resp, "Stuck event requests")
        self.assertContains(resp, "Stuck March")
        self.assertContains(resp, "No authorizers")

    # --- create -------------------------------------------------------------

    def test_create_owner(self):
        self.loginAs(self.manager)
        resp = self.client.post(
            reverse("create-event-owner"),
            {"ownerName": "Mutual Aid", "ownerExpiration": "2030-12-31T18:00"},
        )
        created = EventOwners.objects.get(name="Mutual Aid")
        self.assertRedirects(resp, reverse("manage-event-owner", kwargs={"ownerId": created.id}))
        self.assertFalse(created.isPermanent)
        # Entered as Central (CST, UTC-6 in December), stored as UTC
        self.assertEqual(created.expiration, datetime.datetime(2031, 1, 1, 0, 0, tzinfo=datetime.UTC))

    def test_create_duplicate_name_rejected(self):
        makeOwner("Mutual Aid")
        self.loginAs(self.manager)
        resp = self.client.post(
            reverse("create-event-owner"),
            {"ownerName": "mutual aid", "ownerIsPermanent": "on"},
        )
        self.assertEqual(resp.status_code, 200)  # re-rendered with the error
        self.assertContains(resp, "already exists")
        self.assertEqual(EventOwners.objects.count(), 1)

    def test_create_requires_expiration_or_permanent(self):
        self.loginAs(self.manager)
        resp = self.client.post(reverse("create-event-owner"), {"ownerName": "No Policy"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Set an expiration date or mark the owner as permanent.")
        self.assertFalse(EventOwners.objects.filter(name="No Policy").exists())

    def test_permanent_create_saves_sentinel_expiration(self):
        self.loginAs(self.manager)
        self.client.post(
            reverse("create-event-owner"),
            {"ownerName": "Steering", "ownerIsPermanent": "on"},
        )
        created = EventOwners.objects.get(name="Steering")
        self.assertTrue(created.isPermanent)
        self.assertTrue(created.isActive())
        # Sentinel is only ever written alongside isPermanent=True
        self.assertEqual(created.expiration.year, 2099)

    # --- edit ---------------------------------------------------------------

    def test_edit_name_and_expiration(self):
        owner = makeOwner("Education Committee", authorizers=[self.member])
        self.loginAs(self.manager)
        resp = self.client.post(
            reverse("manage-event-owner", kwargs={"ownerId": owner.id}),
            {"ownerName": "Political Education", "ownerExpiration": "2031-06-15T12:00"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Owner updated.")
        owner.refresh_from_db()
        self.assertEqual(owner.name, "Political Education")
        # Entered as Central (CDT, UTC-5 in June), stored as UTC
        self.assertEqual(owner.expiration, datetime.datetime(2031, 6, 15, 17, 0, tzinfo=datetime.UTC))

    def test_permanent_edit_preserves_prior_expiration(self):
        storedExpiration = datetime.datetime(2032, 3, 1, 12, 0, tzinfo=datetime.UTC)
        owner = makeOwner("Steering", isPermanent=True, expiration=storedExpiration,
                          authorizers=[self.member])
        self.loginAs(self.manager)
        self.client.post(
            reverse("manage-event-owner", kwargs={"ownerId": owner.id}),
            {"ownerName": "Steering", "ownerIsPermanent": "on"},
        )
        owner.refresh_from_db()
        self.assertTrue(owner.isPermanent)
        self.assertEqual(owner.expiration, storedExpiration)

    def test_add_authorizer(self):
        owner = makeOwner("Education Committee", isPermanent=True)
        self.loginAs(self.manager)
        self.client.post(
            reverse("manage-event-owner", kwargs={"ownerId": owner.id}),
            {"ownerName": "Education Committee", "ownerIsPermanent": "on",
             "addAuthorizers": [self.member.id]},
        )
        self.assertIn(self.member, owner.authorizers.all())

    def test_remove_authorizer(self):
        owner = makeOwner("Education Committee", isPermanent=True, authorizers=[self.member])
        self.loginAs(self.manager)
        self.client.post(
            reverse("manage-event-owner", kwargs={"ownerId": owner.id}),
            {"ownerName": "Education Committee", "ownerIsPermanent": "on",
             "removeAuthorizers": [self.member.id]},
        )
        self.assertNotIn(self.member, owner.authorizers.all())

    def test_remove_last_authorizer_blocked_while_requests_pending(self):
        owner = makeOwner("Education Committee", isPermanent=True, authorizers=[self.member])
        makeDelegatedRequest(owner, self.member)
        self.loginAs(self.manager)
        resp = self.client.post(
            reverse("manage-event-owner", kwargs={"ownerId": owner.id}),
            {"ownerName": "Education Committee", "ownerIsPermanent": "on",
             "removeAuthorizers": [self.member.id]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cannot remove the last authorizer")
        self.assertIn(self.member, owner.authorizers.all())

    # --- authorizer search --------------------------------------------------

    def test_authorizer_search_returns_non_authorizers(self):
        owner = makeOwner("Education Committee", isPermanent=True)
        self.loginAs(self.manager)
        resp = self.client.get(
            reverse("manage-event-owner-authorizer-search", kwargs={"ownerId": owner.id}),
            {"q": "rosa"},
        )
        results = resp.json()["results"]
        self.assertEqual([row["username"] for row in results], ["member"])

    def test_authorizer_search_excludes_existing_authorizers(self):
        owner = makeOwner("Education Committee", isPermanent=True, authorizers=[self.member])
        self.loginAs(self.manager)
        resp = self.client.get(
            reverse("manage-event-owner-authorizer-search", kwargs={"ownerId": owner.id}),
            {"q": "rosa"},
        )
        self.assertEqual(resp.json()["results"], [])

    # --- cancel stuck requests ----------------------------------------------

    @patch("tools.ownerViews.EmailApi.sendEmailFromWebsiteAccount")
    def test_cancel_stuck_event_with_zero_authorizers(self, sendEmail):
        orphanOwner = makeOwner("Orphan Committee")
        stuckEvent = makeDelegatedRequest(orphanOwner, self.member)
        self.loginAs(self.manager)
        resp = self.client.post(reverse(
            "cancel-stuck-delegated-event",
            kwargs={"ownerId": orphanOwner.id, "eventId": stuckEvent.id},
        ))
        self.assertRedirects(resp, reverse("manage-event-owner", kwargs={"ownerId": orphanOwner.id}))
        stuckEvent.refresh_from_db()
        self.assertEqual(stuckEvent.status, DelegatedEvents.Status.DENIED)
        self.assertEqual(stuckEvent.approver, self.manager)
        self.assertIn("Cancelled by an event-owner manager", stuckEvent.reason)
        sendEmail.assert_called_once()
        self.assertEqual(sendEmail.call_args.kwargs["toAddress"], self.member.email)

    @patch("tools.ownerViews.EmailApi.sendEmailFromWebsiteAccount")
    def test_cancel_stuck_event_with_expired_owner(self, sendEmail):
        expiredOwner = makeOwner("Old Working Group", expiration=PAST, authorizers=[self.member])
        stuckEvent = makeDelegatedRequest(expiredOwner, self.member)
        self.loginAs(self.manager)
        self.client.post(reverse(
            "cancel-stuck-delegated-event",
            kwargs={"ownerId": expiredOwner.id, "eventId": stuckEvent.id},
        ))
        stuckEvent.refresh_from_db()
        self.assertEqual(stuckEvent.status, DelegatedEvents.Status.DENIED)

    @patch("tools.ownerViews.EmailApi.sendEmailFromWebsiteAccount")
    def test_cancel_blocked_for_healthy_owner(self, sendEmail):
        healthyOwner = makeOwner("Education Committee", isPermanent=True, authorizers=[self.member])
        pendingEvent = makeDelegatedRequest(healthyOwner, self.member)
        self.loginAs(self.manager)
        self.client.post(reverse(
            "cancel-stuck-delegated-event",
            kwargs={"ownerId": healthyOwner.id, "eventId": pendingEvent.id},
        ))
        pendingEvent.refresh_from_db()
        self.assertEqual(pendingEvent.status, DelegatedEvents.Status.REQUESTED)
        sendEmail.assert_not_called()

    @patch("tools.ownerViews.EmailApi.sendEmailFromWebsiteAccount")
    def test_cancel_blocked_for_mismatched_owner_event_pair(self, sendEmail):
        # A healthy owner's pending event POSTed against a stuck owner's id
        # must not be cancellable - stuckness is evaluated on event.owner.
        healthyOwner = makeOwner("Education Committee", isPermanent=True, authorizers=[self.member])
        pendingEvent = makeDelegatedRequest(healthyOwner, self.member)
        stuckOwner = makeOwner("Orphan Committee")
        self.loginAs(self.manager)
        self.client.post(reverse(
            "cancel-stuck-delegated-event",
            kwargs={"ownerId": stuckOwner.id, "eventId": pendingEvent.id},
        ))
        pendingEvent.refresh_from_db()
        self.assertEqual(pendingEvent.status, DelegatedEvents.Status.REQUESTED)
        sendEmail.assert_not_called()

    def test_cancel_rejects_get(self):
        orphanOwner = makeOwner("Orphan Committee")
        stuckEvent = makeDelegatedRequest(orphanOwner, self.member)
        self.loginAs(self.manager)
        resp = self.client.get(reverse(
            "cancel-stuck-delegated-event",
            kwargs={"ownerId": orphanOwner.id, "eventId": stuckEvent.id},
        ))
        self.assertEqual(resp.status_code, 302)
        stuckEvent.refresh_from_db()
        self.assertEqual(stuckEvent.status, DelegatedEvents.Status.REQUESTED)


@fastHashing
class OwnerHealthEnforcementTests(LoginClientMixin, TestCase):
    """The isActive()/healthy-owner gates in the two publish flows and the
    NewEventForm owner dropdown."""

    def setUp(self):
        self.publisher = UserFactory.make("publisher", perms=("publishEvent",))
        self.requester = UserFactory.make("requester", perms=("requestDelegatedEvent",))

    def test_owner_dropdown_excludes_unhealthy_owners(self):
        healthyOwner = makeOwner("Education Committee", authorizers=[self.publisher])
        permanentOwner = makeOwner("Steering", isPermanent=True, expiration=PAST,
                                   authorizers=[self.publisher])
        makeOwner("Old Working Group", expiration=PAST, authorizers=[self.publisher])
        makeOwner("Orphan Committee")  # active but nobody can approve
        selectableOwners = set(NewEventForm().fields[NewEventForm.Keys.OWNER].queryset)
        self.assertEqual(selectableOwners, {healthyOwner, permanentOwner})

    def test_new_event_rejects_unhealthy_owner_as_invalid_choice(self):
        makeOwner("Old Working Group", expiration=PAST, authorizers=[self.publisher])
        self.loginAs(self.publisher)
        with patch("tools.eventViews.EventAutomationDriver.publishEvent") as publishEvent:
            resp = self.client.post(reverse("new-event"), eventFormData("Old Working Group"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "could not be validated")
        self.assertEqual(PostedEvents.objects.count(), 0)
        publishEvent.assert_not_called()

    def test_new_event_view_guard_blocks_expired_owner(self):
        # Exercise the view-level isActive() guard (the fixed bug) directly by
        # letting the expired owner through the form's queryset filter.
        expiredOwner = makeOwner("Old Working Group", expiration=PAST,
                                 authorizers=[self.publisher])
        self.loginAs(self.publisher)
        with patch("tools.forms._activeOwnerQueryset", EventOwners.objects.all), \
             patch("tools.eventViews.EventAutomationDriver.publishEvent") as publishEvent:
            resp = self.client.post(reverse("new-event"), eventFormData(expiredOwner.name))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "no longer active")
        self.assertEqual(PostedEvents.objects.count(), 0)
        publishEvent.assert_not_called()

    def test_new_delegated_event_view_guard_blocks_expired_owner(self):
        expiredOwner = makeOwner("Old Working Group", expiration=PAST,
                                 authorizers=[self.publisher])
        self.loginAs(self.requester)
        with patch("tools.forms._activeOwnerQueryset", EventOwners.objects.all), \
             patch("tools.eventViews.EventAutomationDriver.publishEvent") as publishEvent:
            resp = self.client.post(reverse("new-delegated-event"), eventFormData(expiredOwner.name))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "no longer active")
        self.assertEqual(DelegatedEvents.objects.count(), 0)
        publishEvent.assert_not_called()

    def test_new_delegated_event_rejects_zero_authorizer_owner(self):
        # The dropdown filter is what prevents new silently-unactionable
        # requests: an owner nobody can approve for is not a valid choice.
        makeOwner("Orphan Committee")
        self.loginAs(self.requester)
        with patch("tools.eventViews.EventAutomationDriver.publishEvent") as publishEvent:
            resp = self.client.post(reverse("new-delegated-event"), eventFormData("Orphan Committee"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "could not be validated")
        self.assertEqual(DelegatedEvents.objects.count(), 0)
        publishEvent.assert_not_called()
