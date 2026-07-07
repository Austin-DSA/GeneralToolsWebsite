"""publishEventJob: the Huey task that runs one PublishJob end to end.

EventAutomationDriver.publishEvent is always patched at its tasks-module
binding (tools.tasks.EventAutomationDriver.publishEvent) so no external
service is ever contacted, and task bodies are invoked via .call_local()
per the huey-immediate test idiom (see test_tasks.py).
"""
import datetime
from unittest import mock

import pytz
from django.test import TestCase

from tools import tasks
from tools.EventAutomation import EventAutomationDriver
from tools.eventViews import _buildEventPayload
from tools.models import DelegatedEvents, EventOwners, PostedEvents, PublishJob
from tools.tests.support import UserFactory, fastHashing


FUTURE = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
CHICAGO = pytz.timezone("America/Chicago")


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


def gCalConflict():
    return EventAutomationDriver.Conflict(
        type=EventAutomationDriver.Conflict.ConflictType.GCAL,
        title="Tenant union mixer",
        start=datetime.datetime(2030, 7, 1, 23, 0, tzinfo=datetime.UTC),
        end=datetime.datetime(2030, 7, 2, 0, 30, tzinfo=datetime.UTC),
        zoomUser=None,
    )


def zoomConflict():
    return EventAutomationDriver.Conflict(
        type=EventAutomationDriver.Conflict.ConflictType.ZOOM,
        title="Standing meeting",
        start=datetime.datetime(2030, 7, 1, 23, 0, tzinfo=datetime.UTC),
        end=datetime.datetime(2030, 7, 2, 0, 0, tzinfo=datetime.UTC),
        zoomUser="busy@austindsa.org",
    )


@fastHashing
class PublishEventJobDirectTests(TestCase):
    def setUp(self):
        self.creator = UserFactory.make("publisher")
        self.owner = EventOwners.objects.create(
            name="Education Committee", isPermanent=True, expiration=FUTURE,
        )

    def makeDirectJob(self, ignoreResolveableConflicts=False, **infoOverrides):
        eventInfo = makeEventInfo(**infoOverrides)
        payload = _buildEventPayload(eventInfo, "America/Chicago", ignoreResolveableConflicts)
        job = PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, payload=payload,
            creator=self.creator, owner=self.owner,
        )
        return job, eventInfo

    def runJob(self, job, result=None, sideEffect=None):
        with mock.patch("tools.tasks.EventAutomationDriver.publishEvent",
                        return_value=result, side_effect=sideEffect) as publishEvent, \
             mock.patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount") as sendEmail:
            tasks.publishEventJob.call_local(job.id)
        job.refresh_from_db()
        return publishEvent, sendEmail

    def test_retries_is_zero(self):
        # Load-bearing: Action Network has no delete API, so a retry after a
        # partial run can double-publish. Failures must land in the job row.
        # (Huey stores the task() retries argument as the dynamically-built
        # task class's default_retries.)
        self.assertEqual(tasks.publishEventJob.task_class.default_retries, 0)

    def test_published_creates_posted_event_with_direct_kwargs(self):
        job, eventInfo = self.makeDirectJob()
        publishEvent, sendEmail = self.runJob(job, result=publishedResult())

        self.assertEqual(job.status, PublishJob.Status.PUBLISHED)
        self.assertIsNotNone(job.startedAt)
        self.assertIsNotNone(job.finishedAt)

        event = job.postedEvent
        self.assertIsNotNone(event)
        self.assertEqual(event.title, "Reading Group")
        self.assertEqual(event.start, eventInfo.start.astimezone(pytz.utc))
        self.assertEqual(event.end, eventInfo.end.astimezone(pytz.utc))
        self.assertEqual(event.timezone, "America/Chicago")
        self.assertEqual(event.creator, self.creator)
        self.assertEqual(event.authorizer, self.creator)
        self.assertEqual(event.owner, self.owner)
        self.assertEqual(event.reason, "Created by approved authorizer")
        self.assertEqual(event.anShareLink, "https://an.example/share")
        self.assertEqual(event.anManageLink, "https://an.example/manage")
        self.assertEqual(event.gCalLink, "https://gcal.example/event")
        self.assertEqual(event.zoomLink, "https://zoom.example/j/123")
        self.assertEqual(event.zoomAccount, "events@austindsa.org")
        self.assertTrue(event.zoomRequired)

        sendEmail.assert_called_once()
        self.assertEqual(sendEmail.call_args.kwargs["toAddress"], self.creator.email)

        # The driver was handed the rehydrated (aware) datetimes and a config
        # built from the payload flags - never onlyCheckConflicts.
        calledInfo = publishEvent.call_args.kwargs["eventInfo"]
        self.assertEqual(calledInfo.start, eventInfo.start)
        self.assertEqual(calledInfo.end, eventInfo.end)
        config = publishEvent.call_args.kwargs["config"]
        self.assertFalse(config.ignoreResolveableConflicts)
        self.assertFalse(config.onlyCheckConflicts)

    def test_ignore_flag_passes_through_to_the_driver_config(self):
        job, _ = self.makeDirectJob(ignoreResolveableConflicts=True)
        publishEvent, _ = self.runJob(job, result=publishedResult())
        self.assertTrue(publishEvent.call_args.kwargs["config"].ignoreResolveableConflicts)

    def test_conflict_result_populates_conflicts_and_no_posted_event(self):
        job, _ = self.makeDirectJob()
        result = EventAutomationDriver.Result(
            type=EventAutomationDriver.Result.ResultType.CONFLICT,
            conflicts=[gCalConflict()],
        )
        _, sendEmail = self.runJob(job, result=result)
        self.assertEqual(job.status, PublishJob.Status.CONFLICT)
        self.assertEqual(PostedEvents.objects.count(), 0)
        self.assertIsNone(job.postedEvent)
        sendEmail.assert_not_called()
        expectedStartIso = (gCalConflict().start.astimezone(CHICAGO)
                            .replace(tzinfo=None).isoformat())
        self.assertEqual(job.conflicts, [{
            "type": EventAutomationDriver.Conflict.ConflictType.GCAL,
            "title": "Tenant union mixer",
            "zoomUser": None,
            "startIso": expectedStartIso,
            "endIso": (gCalConflict().end.astimezone(CHICAGO)
                       .replace(tzinfo=None).isoformat()),
        }])

    def test_unresolveable_result_populates_conflicts(self):
        job, _ = self.makeDirectJob()
        result = EventAutomationDriver.Result(
            type=EventAutomationDriver.Result.ResultType.UNRESOLVEABLE_CONFLICT,
            conflicts=[zoomConflict()],
        )
        self.runJob(job, result=result)
        self.assertEqual(job.status, PublishJob.Status.UNRESOLVEABLE)
        self.assertEqual(job.conflicts[0]["zoomUser"], "busy@austindsa.org")
        self.assertEqual(PostedEvents.objects.count(), 0)

    def test_unexpected_result_marks_failed_with_joined_error(self):
        job, _ = self.makeDirectJob()
        result = EventAutomationDriver.Result(
            type=EventAutomationDriver.Result.ResultType.UNEXPECTED,
            errorStr=["boom: ", "bang"],  # format_exception returns a list
        )
        self.runJob(job, result=result)
        self.assertEqual(job.status, PublishJob.Status.FAILED)
        self.assertEqual(job.errorMessage, "boom: bang")
        self.assertEqual(PostedEvents.objects.count(), 0)

    def test_raised_exception_marks_failed_with_traceback(self):
        # publishEvent is documented never to raise, but the broad except is
        # the worker's last line - nothing may escape to Huey's retry layer.
        job, _ = self.makeDirectJob()
        self.runJob(job, sideEffect=Exception("kaboom"))
        self.assertEqual(job.status, PublishJob.Status.FAILED)
        self.assertIn("kaboom", job.errorMessage)
        self.assertIsNotNone(job.finishedAt)

    def test_payload_version_mismatch_fails_without_publishing(self):
        job, _ = self.makeDirectJob()
        job.payload = {**job.payload, "payloadVersion": 99}
        job.save()
        publishEvent, _ = self.runJob(job)
        publishEvent.assert_not_called()
        self.assertEqual(job.status, PublishJob.Status.FAILED)
        self.assertIn("payload version", job.errorMessage)

    def test_missing_job_logs_and_returns(self):
        with mock.patch("tools.tasks.EventAutomationDriver.publishEvent") as publishEvent, \
             self.assertLogs("tools.tasks", level="ERROR"):
            tasks.publishEventJob.call_local(987654)
        publishEvent.assert_not_called()


@fastHashing
class PublishEventJobDelegatedTests(TestCase):
    def setUp(self):
        self.requester = UserFactory.make("requester")
        self.approver = UserFactory.make("approver")
        self.owner = EventOwners.objects.create(
            name="Education Committee", isPermanent=True, expiration=FUTURE,
        )
        self.event = DelegatedEvents.objects.create(
            title="Tabling at the farmers market",
            start=datetime.datetime(2030, 7, 1, 23, 0, tzinfo=datetime.UTC),
            end=datetime.datetime(2030, 7, 2, 0, 0, tzinfo=datetime.UTC),
            timezone="America/Chicago",
            locationName="Mueller Lake Park", streetAddress="4550 Mueller Blvd",
            city="Austin", state="TX", zip="78723", country="US",
            description="A table, some flyers", instructions="Look for the red banner",
            dateCreated=datetime.datetime.now(datetime.UTC),
            creator=self.requester, owner=self.owner,
            status=DelegatedEvents.Status.REQUESTED,
        )

    def makeDelegatedJob(self, reason="Looks good"):
        eventInfo = self.event.getEventInfo()
        payload = _buildEventPayload(eventInfo, self.event.timezone, ignoreResolveableConflicts=True)
        payload["reason"] = reason
        payload["approverId"] = self.approver.id
        return PublishJob.objects.create(
            kind=PublishJob.Kind.DELEGATED, payload=payload,
            creator=self.approver, owner=self.owner, delegatedEvent=self.event,
        )

    def test_published_flips_the_request_before_creating_the_posted_event(self):
        job = self.makeDelegatedJob()
        statusWhenPostedEventCreated = {}
        realCreate = PostedEvents.objects.create

        def guardedCreate(**kwargs):
            # Capture the request row's DB status at the moment the
            # PostedEvents row is created: the flip must already be saved
            # (today's flip-before-PostedEvents ordering, a hard constraint).
            statusWhenPostedEventCreated["status"] = (
                DelegatedEvents.objects.get(id=self.event.id).status
            )
            return realCreate(**kwargs)

        with mock.patch("tools.tasks.EventAutomationDriver.publishEvent",
                        return_value=publishedResult()), \
             mock.patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount"), \
             mock.patch.object(PostedEvents.objects, "create", side_effect=guardedCreate):
            tasks.publishEventJob.call_local(job.id)

        self.assertEqual(statusWhenPostedEventCreated["status"], DelegatedEvents.Status.APPROVED)

        job.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(job.status, PublishJob.Status.PUBLISHED)
        self.assertEqual(self.event.status, DelegatedEvents.Status.APPROVED)
        self.assertEqual(self.event.approver, self.approver)  # from payload approverId
        self.assertIsNotNone(self.event.dateReviewed)
        self.assertEqual(self.event.reason, "Looks good")

        event = job.postedEvent
        self.assertEqual(event.title, self.event.title)
        self.assertEqual(event.start, self.event.start)  # already UTC on the row
        self.assertEqual(event.end, self.event.end)
        self.assertEqual(event.timezone, self.event.timezone)
        self.assertEqual(event.creator, self.requester)
        self.assertEqual(event.authorizer, self.approver)
        self.assertEqual(event.owner, self.owner)
        self.assertEqual(event.reason, "Looks good")
        self.assertEqual(event.anShareLink, "https://an.example/share")

    def test_published_emails_approver_and_requester(self):
        job = self.makeDelegatedJob()
        with mock.patch("tools.tasks.EventAutomationDriver.publishEvent",
                        return_value=publishedResult()), \
             mock.patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount") as sendEmail:
            tasks.publishEventJob.call_local(job.id)
        self.assertEqual(sendEmail.call_count, 2)
        recipients = {call.kwargs["toAddress"] for call in sendEmail.call_args_list}
        self.assertEqual(recipients, {self.approver.email, self.requester.email})

    def test_unresolveable_leaves_the_request_requested(self):
        job = self.makeDelegatedJob()
        result = EventAutomationDriver.Result(
            type=EventAutomationDriver.Result.ResultType.UNRESOLVEABLE_CONFLICT,
            conflicts=[zoomConflict()],
        )
        with mock.patch("tools.tasks.EventAutomationDriver.publishEvent", return_value=result), \
             mock.patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount") as sendEmail:
            tasks.publishEventJob.call_local(job.id)
        job.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(job.status, PublishJob.Status.UNRESOLVEABLE)
        self.assertEqual(self.event.status, DelegatedEvents.Status.REQUESTED)
        self.assertEqual(PostedEvents.objects.count(), 0)
        sendEmail.assert_not_called()

    def test_deleted_approver_lands_in_failed(self):
        # approverId pointing at a user deleted between enqueue and run
        # surfaces through the broad except as FAILED (visible in admin).
        job = self.makeDelegatedJob()
        job.payload = {**job.payload, "approverId": 987654}
        job.save()
        with mock.patch("tools.tasks.EventAutomationDriver.publishEvent",
                        return_value=publishedResult()), \
             mock.patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount"):
            tasks.publishEventJob.call_local(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, PublishJob.Status.FAILED)
        self.assertIn("DoesNotExist", job.errorMessage)


@fastHashing
class PublishEventJobDemoModeTests(TestCase):
    """DEMO_MODE now lives inside the task and deliberately stubs BOTH kinds -
    the old inline approve flow had no demo path and would attempt (and fail)
    a real publish on the demo box.

    tools/tasks.py reads the raw settings module (`import settings`, the
    tools/ convention), which override_settings cannot reach - so these tests
    patch the module attribute directly."""

    def setUp(self):
        self.creator = UserFactory.make("publisher")
        self.requester = UserFactory.make("requester")
        self.approver = UserFactory.make("approver")
        self.owner = EventOwners.objects.create(
            name="Education Committee", isPermanent=True, expiration=FUTURE,
        )

    def makeDirectJob(self, **infoOverrides):
        payload = _buildEventPayload(makeEventInfo(**infoOverrides), "America/Chicago", False)
        return PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, payload=payload,
            creator=self.creator, owner=self.owner,
        )

    def runDemoJob(self, job):
        with mock.patch.object(tasks.settings, "DEMO_MODE", True), \
             mock.patch("tools.tasks.time.sleep") as sleep, \
             mock.patch("tools.tasks.EventAutomationDriver.publishEvent") as publishEvent, \
             mock.patch("tools.tasks.EmailApi.sendEmailFromWebsiteAccount"):
            tasks.publishEventJob.call_local(job.id)
        job.refresh_from_db()
        return publishEvent, sleep

    def test_direct_demo_stubs_a_published_result(self):
        job = self.makeDirectJob()
        publishEvent, sleep = self.runDemoJob(job)
        publishEvent.assert_not_called()
        sleep.assert_called_once_with(2)
        self.assertEqual(job.status, PublishJob.Status.PUBLISHED)
        event = job.postedEvent
        self.assertEqual(event.anShareLink, "https://actionnetwork.org/events/demo-event")
        self.assertEqual(event.anManageLink, "https://actionnetwork.org/events/demo-event/manage")
        self.assertEqual(event.gCalLink, "https://calendar.google.com/calendar/u/0/r/eventedit")
        self.assertEqual(event.zoomLink, "https://us02web.zoom.us/j/0000000000")
        self.assertEqual(event.zoomAccount, "events@austindsa.org (demo)")

    def test_direct_demo_without_zoom_blanks_the_zoom_fields(self):
        job = self.makeDirectJob(zoomRequired=False)
        self.runDemoJob(job)
        event = job.postedEvent
        self.assertEqual(event.zoomLink, "")
        self.assertEqual(event.zoomAccount, "")

    def test_delegated_demo_short_circuits_the_real_publish(self):
        # The explicit assertion of the intentional behavior change: a
        # delegated approval on the demo box never reaches the driver and
        # still walks the full approve bookkeeping.
        event = DelegatedEvents.objects.create(
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
        payload = _buildEventPayload(event.getEventInfo(), event.timezone, ignoreResolveableConflicts=True)
        payload["reason"] = "Demo approve"
        payload["approverId"] = self.approver.id
        job = PublishJob.objects.create(
            kind=PublishJob.Kind.DELEGATED, payload=payload,
            creator=self.approver, owner=self.owner, delegatedEvent=event,
        )
        publishEvent, _ = self.runDemoJob(job)
        publishEvent.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(job.status, PublishJob.Status.PUBLISHED)
        self.assertEqual(event.status, DelegatedEvents.Status.APPROVED)
        self.assertEqual(event.approver, self.approver)
        self.assertEqual(job.postedEvent.anShareLink, "https://actionnetwork.org/events/demo-event")
