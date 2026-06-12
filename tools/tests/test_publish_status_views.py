"""The publish-status page (spinner -> existing result templates), its JSON
poll endpoint, and publish-anyway.

Huey runs in immediate mode under tests, so a POST to the publish views
executes publishEventJob inline within the request - the redirect already
points at a terminal status page. PENDING-state tests therefore create the
job row directly and never enqueue.
"""
import datetime
from unittest.mock import patch

import pytz
from django.test import TestCase
from django.urls import reverse

from tools.EventAutomation import EventAutomationDriver
from tools.eventViews import _buildEventPayload
from tools.models import DelegatedEvents, EventOwners, PostedEvents, PublishJob
from tools.tests.support import LoginClientMixin, UserFactory, fastHashing


FUTURE = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
CHICAGO = pytz.timezone("America/Chicago")

SPINNER_COPY = "Publishing to Zoom, Action Network, and Google Calendar..."
STALE_COPY = "This is taking longer than expected."


def makeOwner(name, authorizers=()):
    owner = EventOwners.objects.create(name=name, isPermanent=True, expiration=FUTURE)
    for authorizer in authorizers:
        owner.authorizers.add(authorizer)
    return owner


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


def makeEventInfo(**overrides):
    fields = dict(
        title="Reading Group",
        eventType=2,  # HYBRID
        start=CHICAGO.localize(datetime.datetime(2030, 7, 1, 18, 0)),
        end=CHICAGO.localize(datetime.datetime(2030, 7, 1, 19, 0)),
        locationName="Little Walnut Creek Library",
        streetAddress="835 W Rundberg Ln",
        city="Austin",
        state="TX",
        zip=78758,
        description="Chapter reading group",
        instructions="Look for the red banner",
        country="US",
        zoomRequired=True,
    )
    fields.update(overrides)
    return EventAutomationDriver.EventInfo(**fields)


def publishedResult(**overrides):
    fields = dict(
        type=EventAutomationDriver.Result.ResultType.PUBLISHED,
        anManageLink="https://an.example/manage",
        anShareLink="https://an.example/share",
        gCalLink="https://gcal.example/event",
        zoomLink="https://zoom.example/j/123",
        zoomAccount="events@austindsa.org",
    )
    fields.update(overrides)
    return EventAutomationDriver.Result(**fields)


def serializedGCalConflict(title="Tenant union mixer"):
    return {
        "type": EventAutomationDriver.Conflict.ConflictType.GCAL,
        "title": title,
        "zoomUser": None,
        "startIso": "2030-07-01T18:00:00",
        "endIso": "2030-07-01T19:30:00",
    }


def makePostedEvent(**overrides):
    eventMoment = datetime.datetime(2030, 7, 1, 23, 0, tzinfo=datetime.UTC)
    fields = dict(
        title="Reading Group", start=eventMoment, end=eventMoment,
        timezone="America/Chicago",
        locationName="", streetAddress="", city="", state="", zip="", country="",
        description="", instructions="",
        dateCreated=eventMoment, datePublished=eventMoment,
        anManageLink="https://an.example/manage",
        anShareLink="https://an.example/share",
        gCalLink="https://gcal.example/event",
        zoomLink="https://zoom.example/j/123",
        zoomAccount="events@austindsa.org",
        reason="",
    )
    fields.update(overrides)
    return PostedEvents.objects.create(**fields)


def statusUrl(job):
    return reverse("publish-status", kwargs={"jobId": job.id})


def statusJsonUrl(job):
    return reverse("publish-status-json", kwargs={"jobId": job.id})


def publishAnywayUrl(job):
    return reverse("publish-publish-anyway", kwargs={"jobId": job.id})


@fastHashing
class NewEventEnqueueTests(LoginClientMixin, TestCase):
    """The rewritten new_event POST tail: validate, create a DIRECT job,
    run inline (immediate mode), redirect to the status page."""

    def setUp(self):
        self.publisher = UserFactory.make("publisher", perms=("publishEvent",))
        self.owner = makeOwner("Education Committee", authorizers=[self.publisher])

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_post_creates_job_and_redirects_to_status_page(self, publishEvent, sendEmail):
        publishEvent.return_value = publishedResult()
        self.loginAs(self.publisher)
        resp = self.client.post(reverse("new-event"), eventFormData("Education Committee"))
        job = PublishJob.objects.get()
        self.assertEqual(job.kind, PublishJob.Kind.DIRECT)
        self.assertEqual(job.creator, self.publisher)
        self.assertEqual(job.owner, self.owner)
        self.assertEqual(job.payload["title"], "Reading Group")
        self.assertEqual(job.payload["timezone"], "US/Central")
        # Immediate mode ran the task inline within the POST.
        self.assertEqual(job.status, PublishJob.Status.PUBLISHED)
        self.assertRedirects(resp, statusUrl(job))

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_redirect_target_renders_the_existing_success_template(self, publishEvent, sendEmail):
        publishEvent.return_value = publishedResult()
        self.loginAs(self.publisher)
        resp = self.client.post(reverse("new-event"), eventFormData("Education Committee"), follow=True)
        self.assertTemplateUsed(resp, "tools/new-event/published.html")
        self.assertContains(resp, "https://an.example/share")
        self.assertContains(resp, "https://gcal.example/event")


@fastHashing
class ApproveDelegatedEnqueueTests(LoginClientMixin, TestCase):
    """The rewritten approve-branch tail: gates kept, DELEGATED job enqueued,
    redirect to the status page (which lands on the detail page once
    published, today's destination)."""

    def setUp(self):
        self.requester = UserFactory.make("requester")
        self.approver = UserFactory.make("approver", perms=("approveDelegatedEvent",))
        self.owner = makeOwner("Education Committee", authorizers=[self.approver])
        self.event = DelegatedEvents.objects.create(
            title="Tabling at the farmers market",
            start=datetime.datetime(2030, 7, 1, 23, 0, tzinfo=datetime.UTC),
            end=datetime.datetime(2030, 7, 2, 0, 0, tzinfo=datetime.UTC),
            timezone="America/Chicago",
            locationName="", streetAddress="", city="Austin", state="TX",
            zip="", country="US", description="Flyers", instructions="",
            dateCreated=datetime.datetime.now(datetime.UTC),
            creator=self.requester, owner=self.owner,
            status=DelegatedEvents.Status.REQUESTED,
        )

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_approve_post_creates_delegated_job_and_redirects(self, publishEvent, sendEmail):
        publishEvent.return_value = publishedResult()
        self.loginAs(self.approver)
        resp = self.client.post(
            reverse("approve-delegated-event", kwargs={"id": self.event.id}),
            {"approve": "YES", "reason": "Looks good"},
        )
        job = PublishJob.objects.get()
        self.assertEqual(job.kind, PublishJob.Kind.DELEGATED)
        self.assertEqual(job.delegatedEvent, self.event)
        self.assertEqual(job.creator, self.approver)
        self.assertTrue(job.payload["ignoreResolveableConflicts"])  # forced by the approve flow
        self.assertEqual(job.payload["approverId"], self.approver.id)
        self.assertEqual(job.payload["reason"], "Looks good")
        self.assertRedirects(resp, statusUrl(job), fetch_redirect_response=False)
        # Inline run flipped the request and published.
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, DelegatedEvents.Status.APPROVED)
        self.assertEqual(job.status, PublishJob.Status.PUBLISHED)

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_published_delegated_status_page_redirects_to_detail(self, publishEvent, sendEmail):
        publishEvent.return_value = publishedResult()
        self.loginAs(self.approver)
        self.client.post(
            reverse("approve-delegated-event", kwargs={"id": self.event.id}),
            {"approve": "YES", "reason": ""},
        )
        job = PublishJob.objects.get()
        resp = self.client.get(statusUrl(job))
        self.assertRedirects(
            resp,
            reverse("delegated-event-detail", kwargs={"pk": self.event.id}),
            fetch_redirect_response=False,  # the detail view needs its own permission
        )

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_deny_branch_is_unchanged_and_enqueues_nothing(self, publishEvent, sendEmail):
        self.loginAs(self.approver)
        resp = self.client.post(
            reverse("approve-delegated-event", kwargs={"id": self.event.id}),
            {"approve": "NO", "reason": "Conflicts with the GBM"},
        )
        self.assertRedirects(
            resp,
            reverse("delegated-event-detail", kwargs={"pk": self.event.id}),
            fetch_redirect_response=False,
        )
        self.assertEqual(PublishJob.objects.count(), 0)
        publishEvent.assert_not_called()
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, DelegatedEvents.Status.DENIED)


@fastHashing
class PublishStatusPageTests(LoginClientMixin, TestCase):
    def setUp(self):
        self.creator = UserFactory.make("creator")
        self.other = UserFactory.make("other")
        self.superuser = UserFactory.superuser("super")

    def makeJob(self, status=PublishJob.Status.PENDING, kind=PublishJob.Kind.DIRECT, **kwargs):
        return PublishJob.objects.create(
            kind=kind, status=status, payload={"title": "Reading Group"},
            creator=self.creator, **kwargs,
        )

    # --- access control -----------------------------------------------------

    def test_anonymous_user_redirected_to_login(self):
        job = self.makeJob()
        resp = self.client.get(statusUrl(job))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_non_creator_gets_403(self):
        job = self.makeJob()
        self.loginAs(self.other)
        self.assertEqual(self.client.get(statusUrl(job)).status_code, 403)
        self.assertEqual(self.client.get(statusJsonUrl(job)).status_code, 403)

    def test_superuser_can_view(self):
        job = self.makeJob()
        self.loginAs(self.superuser)
        self.assertEqual(self.client.get(statusUrl(job)).status_code, 200)
        self.assertEqual(self.client.get(statusJsonUrl(job)).status_code, 200)

    def test_missing_job_404s(self):
        self.loginAs(self.creator)
        resp = self.client.get(reverse("publish-status", kwargs={"jobId": 987654}))
        self.assertEqual(resp.status_code, 404)

    # --- pending / running: the spinner --------------------------------------

    def test_pending_job_renders_the_spinner(self):
        job = self.makeJob()  # created directly, never enqueued
        self.loginAs(self.creator)
        resp = self.client.get(statusUrl(job))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "tools/publish-status/status.html")
        self.assertContains(resp, SPINNER_COPY)
        self.assertContains(resp, statusJsonUrl(job))
        self.assertContains(resp, STALE_COPY)  # hidden note, revealed by the poll script
        self.assertContains(resp, reverse("event-list"))

    def test_running_job_renders_the_spinner(self):
        job = self.makeJob(status=PublishJob.Status.RUNNING)
        self.loginAs(self.creator)
        resp = self.client.get(statusUrl(job))
        self.assertTemplateUsed(resp, "tools/publish-status/status.html")

    # --- the JSON poll contract ----------------------------------------------

    def test_json_contract_for_a_pending_job(self):
        job = self.makeJob()
        self.loginAs(self.creator)
        data = self.client.get(statusJsonUrl(job)).json()
        self.assertEqual(set(data), {"status", "statusLabel", "isTerminal", "createdAtIso"})
        self.assertEqual(data["status"], PublishJob.Status.PENDING)
        self.assertEqual(data["statusLabel"], "Pending")
        self.assertFalse(data["isTerminal"])
        self.assertEqual(datetime.datetime.fromisoformat(data["createdAtIso"]), job.createdAt)

    def test_json_reports_terminal_for_a_finished_job(self):
        job = self.makeJob(status=PublishJob.Status.PUBLISHED, postedEvent=makePostedEvent())
        self.loginAs(self.creator)
        data = self.client.get(statusJsonUrl(job)).json()
        self.assertTrue(data["isTerminal"])
        self.assertEqual(data["statusLabel"], "Published")

    # --- terminal renders: the (status x kind) table --------------------------

    def test_published_direct_renders_published_template(self):
        job = self.makeJob(status=PublishJob.Status.PUBLISHED, postedEvent=makePostedEvent())
        self.loginAs(self.creator)
        resp = self.client.get(statusUrl(job))
        self.assertTemplateUsed(resp, "tools/new-event/published.html")
        self.assertContains(resp, "https://an.example/share")
        self.assertContains(resp, "https://an.example/manage")
        self.assertContains(resp, "https://gcal.example/event")
        self.assertContains(resp, "https://zoom.example/j/123")
        self.assertContains(resp, "events@austindsa.org")

    def test_conflict_direct_renders_resolveable_with_publish_anyway(self):
        job = self.makeJob(status=PublishJob.Status.CONFLICT, conflicts=[serializedGCalConflict()])
        self.loginAs(self.creator)
        resp = self.client.get(statusUrl(job))
        self.assertTemplateUsed(resp, "tools/new-event/resolveable.html")
        self.assertContains(resp, "Tenant union mixer")
        self.assertContains(resp, "Publish anyway")
        self.assertContains(resp, publishAnywayUrl(job))

    def test_conflict_delegated_defensively_renders_approve_unresolveable(self):
        # Unreachable in practice (the approve flow forces the ignore flag) -
        # the defensive render keeps a surprise CONFLICT from 500ing.
        job = self.makeJob(
            status=PublishJob.Status.CONFLICT, kind=PublishJob.Kind.DELEGATED,
            conflicts=[serializedGCalConflict()],
        )
        self.loginAs(self.creator)
        resp = self.client.get(statusUrl(job))
        self.assertTemplateUsed(resp, "tools/approve-delegated-event/unresolveable.html")

    def test_unresolveable_renders_per_kind(self):
        templateByKind = (
            (PublishJob.Kind.DIRECT, "tools/new-event/unresolveable.html"),
            (PublishJob.Kind.DELEGATED, "tools/approve-delegated-event/unresolveable.html"),
        )
        self.loginAs(self.creator)
        for kind, template in templateByKind:
            with self.subTest(kind=kind):
                job = self.makeJob(
                    status=PublishJob.Status.UNRESOLVEABLE, kind=kind,
                    conflicts=[serializedGCalConflict(title="Standing meeting")],
                )
                resp = self.client.get(statusUrl(job))
                self.assertTemplateUsed(resp, template)
                self.assertContains(resp, "Standing meeting")

    def test_failed_renders_per_kind_with_the_error(self):
        templateByKind = (
            (PublishJob.Kind.DIRECT, "tools/new-event/unknown.html"),
            (PublishJob.Kind.DELEGATED, "tools/approve-delegated-event/unknown.html"),
        )
        self.loginAs(self.creator)
        for kind, template in templateByKind:
            with self.subTest(kind=kind):
                job = self.makeJob(status=PublishJob.Status.FAILED, kind=kind)
                job.errorMessage = "Traceback: kaboom"
                job.save()
                resp = self.client.get(statusUrl(job))
                self.assertTemplateUsed(resp, template)
                self.assertContains(resp, "Traceback: kaboom")


@fastHashing
class PublishAnywayTests(LoginClientMixin, TestCase):
    def setUp(self):
        self.creator = UserFactory.make("creator")
        self.other = UserFactory.make("other")
        self.owner = makeOwner("Education Committee", authorizers=[self.creator])

    def makeConflictJob(self, **payloadOverrides):
        payload = _buildEventPayload(makeEventInfo(), "America/Chicago", False)
        payload.update(payloadOverrides)
        return PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.CONFLICT,
            payload=payload, conflicts=[serializedGCalConflict()],
            creator=self.creator, owner=self.owner,
        )

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_clones_the_job_with_the_ignore_flag_and_redirects(self, publishEvent, sendEmail):
        publishEvent.return_value = publishedResult()
        job = self.makeConflictJob()
        self.loginAs(self.creator)
        resp = self.client.post(publishAnywayUrl(job))
        newJob = PublishJob.objects.exclude(id=job.id).get()
        self.assertEqual(newJob.kind, PublishJob.Kind.DIRECT)
        self.assertEqual(newJob.creator, self.creator)
        self.assertEqual(newJob.owner, self.owner)
        self.assertTrue(newJob.payload["ignoreResolveableConflicts"])
        self.assertEqual(newJob.payload["title"], job.payload["title"])
        self.assertRedirects(resp, statusUrl(newJob))
        # Immediate mode published the clone inline; the source job is untouched.
        self.assertEqual(newJob.status, PublishJob.Status.PUBLISHED)
        job.refresh_from_db()
        self.assertEqual(job.status, PublishJob.Status.CONFLICT)

    def test_get_is_rejected_with_405(self):
        job = self.makeConflictJob()
        self.loginAs(self.creator)
        resp = self.client.get(publishAnywayUrl(job))
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(PublishJob.objects.count(), 1)

    def test_non_creator_gets_403(self):
        job = self.makeConflictJob()
        self.loginAs(self.other)
        resp = self.client.post(publishAnywayUrl(job))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(PublishJob.objects.count(), 1)

    def test_rejects_a_job_that_is_not_a_direct_conflict(self):
        self.loginAs(self.creator)
        publishedJob = PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.PUBLISHED,
            payload={}, creator=self.creator,
        )
        delegatedConflict = PublishJob.objects.create(
            kind=PublishJob.Kind.DELEGATED, status=PublishJob.Status.CONFLICT,
            payload={}, creator=self.creator,
        )
        for job in (publishedJob, delegatedConflict):
            with self.subTest(job=job.id):
                resp = self.client.post(publishAnywayUrl(job))
                self.assertEqual(resp.status_code, 400)
        self.assertEqual(PublishJob.objects.count(), 2)  # nothing cloned

    def test_sibling_dedup_blocks_a_duplicate_and_shows_the_existing_job(self):
        # The server-side guard: a same-creator/same-event job already in
        # flight means no second job, ever - the JS button-disable is only
        # best-effort.
        job = self.makeConflictJob()
        sibling = PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.RUNNING,
            payload={**job.payload, "ignoreResolveableConflicts": True},
            creator=self.creator, owner=self.owner,
        )
        self.loginAs(self.creator)
        resp = self.client.post(publishAnywayUrl(job))
        self.assertEqual(PublishJob.objects.count(), 2)  # no clone created
        self.assertRedirects(resp, statusUrl(sibling))

    def test_published_sibling_also_blocks(self):
        job = self.makeConflictJob()
        sibling = PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.PUBLISHED,
            payload=dict(job.payload), creator=self.creator, owner=self.owner,
            postedEvent=makePostedEvent(),
        )
        self.loginAs(self.creator)
        resp = self.client.post(publishAnywayUrl(job))
        self.assertEqual(PublishJob.objects.count(), 2)
        self.assertRedirects(resp, statusUrl(sibling))

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_failed_sibling_does_not_block_a_retry(self, publishEvent, sendEmail):
        publishEvent.return_value = publishedResult()
        job = self.makeConflictJob()
        PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.FAILED,
            payload=dict(job.payload), creator=self.creator, owner=self.owner,
        )
        self.loginAs(self.creator)
        resp = self.client.post(publishAnywayUrl(job))
        self.assertEqual(PublishJob.objects.count(), 3)  # the clone was created
        newJob = PublishJob.objects.order_by("-id").first()
        self.assertRedirects(resp, statusUrl(newJob))

    @patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount")
    @patch("tools.tasks.EventAutomationDriver.publishEvent")
    def test_a_different_event_does_not_block(self, publishEvent, sendEmail):
        # The dedup key is (creator, title, startIso) - someone publishing two
        # different events back to back must not be blocked.
        publishEvent.return_value = publishedResult()
        job = self.makeConflictJob()
        otherPayload = _buildEventPayload(
            makeEventInfo(title="Different Event"), "America/Chicago", False,
        )
        PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.RUNNING,
            payload=otherPayload, creator=self.creator, owner=self.owner,
        )
        self.loginAs(self.creator)
        self.client.post(publishAnywayUrl(job))
        self.assertEqual(PublishJob.objects.count(), 3)  # clone created despite the other job
