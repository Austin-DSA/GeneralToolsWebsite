"""PublishJob: the payload and conflict serialization round trips, the status
helpers, and the status -> template-context map (getResultContext) the
publish-status page consumes.

Serialization is written by eventViews._buildEventPayload and read back by
tasks._rehydrateEventInfo / PublishJob.getResultContext - these tests pin the
round trip field by field so a schema drift fails here, not in a worker.
"""
import datetime

import pytz
from django.test import TestCase
from django.urls import reverse

from tools import tasks
from tools.EventAutomation import EventAutomationDriver
from tools.eventViews import _buildEventPayload
from tools.models import PostedEvents, PublishJob


CHICAGO = pytz.timezone("America/Chicago")


def makeEventInfo(**overrides):
    """An EventInfo the way the form builds one: naive local times localized
    to the form timezone (pytz), zip still an int from the form's clean."""
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


class PayloadRoundTripTests(TestCase):
    def test_payload_round_trips_field_by_field(self):
        eventInfo = makeEventInfo()
        payload = _buildEventPayload(eventInfo, "America/Chicago", ignoreResolveableConflicts=False)
        self.assertEqual(payload["payloadVersion"], PublishJob.PAYLOAD_VERSION)
        self.assertEqual(payload["timezone"], "America/Chicago")
        self.assertFalse(payload["ignoreResolveableConflicts"])

        rehydrated = tasks._rehydrateEventInfo(payload)
        self.assertEqual(rehydrated.title, eventInfo.title)
        self.assertEqual(rehydrated.eventType, eventInfo.eventType)
        # fromisoformat yields a fixed-offset tzinfo, not pytz - equality is
        # by instant (==), never by tzinfo identity.
        self.assertEqual(rehydrated.start, eventInfo.start)
        self.assertEqual(rehydrated.end, eventInfo.end)
        # The driver rejects naive datetimes; the round trip must stay aware.
        self.assertIsNotNone(rehydrated.start.utcoffset())
        self.assertIsNotNone(rehydrated.end.utcoffset())
        self.assertEqual(rehydrated.locationName, eventInfo.locationName)
        self.assertEqual(rehydrated.streetAddress, eventInfo.streetAddress)
        self.assertEqual(rehydrated.city, eventInfo.city)
        self.assertEqual(rehydrated.state, eventInfo.state)
        self.assertEqual(rehydrated.zip, "78758")  # coerced to str at serialize time
        self.assertEqual(rehydrated.country, eventInfo.country)
        self.assertEqual(rehydrated.description, eventInfo.description)
        self.assertEqual(rehydrated.instructions, eventInfo.instructions)
        self.assertTrue(rehydrated.zoomRequired)

    def test_payload_carries_the_ignore_flag(self):
        payload = _buildEventPayload(makeEventInfo(), "America/Chicago", ignoreResolveableConflicts=True)
        self.assertIs(payload["ignoreResolveableConflicts"], True)

    def test_empty_zip_serializes_to_empty_string(self):
        # clean_zipcode returns "" when the field is blank.
        payload = _buildEventPayload(makeEventInfo(zip=""), "America/Chicago", False)
        self.assertEqual(payload["zip"], "")


class StatusHelperTests(TestCase):
    STATUS_TABLE = (
        (PublishJob.Status.PENDING, "Pending", False),
        (PublishJob.Status.RUNNING, "Running", False),
        (PublishJob.Status.PUBLISHED, "Published", True),
        (PublishJob.Status.CONFLICT, "Conflict", True),
        (PublishJob.Status.UNRESOLVEABLE, "Unresolveable Conflict", True),
        (PublishJob.Status.FAILED, "Failed", True),
    )

    def test_status_string_and_terminality_per_status(self):
        for status, label, terminal in self.STATUS_TABLE:
            with self.subTest(status=status):
                job = PublishJob(kind=PublishJob.Kind.DIRECT, status=status, payload={})
                self.assertEqual(job.getStatusAsString(), label)
                self.assertEqual(job.isTerminal(), terminal)

    def test_unknown_status_is_labeled(self):
        job = PublishJob(kind=PublishJob.Kind.DIRECT, status=99, payload={})
        self.assertEqual(job.getStatusAsString(), "Unknown 99")
        self.assertFalse(job.isTerminal())

    def test_kind_strings(self):
        self.assertEqual(PublishJob(kind=PublishJob.Kind.DIRECT, payload={}).getKindAsString(), "Direct")
        self.assertEqual(PublishJob(kind=PublishJob.Kind.DELEGATED, payload={}).getKindAsString(), "Delegated")

    def test_status_url_points_at_the_status_page(self):
        job = PublishJob.objects.create(kind=PublishJob.Kind.DIRECT, payload={})
        self.assertEqual(job.getStatusUrl(), reverse("publish-status", kwargs={"jobId": job.id}))


class ResultContextTests(TestCase):
    def test_published_context_has_the_five_link_keys(self):
        event = makePostedEvent()
        job = PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.PUBLISHED,
            payload={}, postedEvent=event,
        )
        self.assertEqual(job.getResultContext(), {
            "anShareLink": "https://an.example/share",
            "anManageLink": "https://an.example/manage",
            "gCalLink": "https://gcal.example/event",
            "zoomLink": "https://zoom.example/j/123",
            "zoomAccount": "events@austindsa.org",
        })

    def test_published_context_survives_a_deleted_posted_event(self):
        # postedEvent is SET_NULL - the page should degrade, not crash.
        job = PublishJob(kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.PUBLISHED, payload={})
        self.assertEqual(job.getResultContext()["anShareLink"], "")

    def test_conflict_context_round_trips_the_localize_then_strip(self):
        # The serialized ISO strings must reconstruct exactly the naive,
        # payload-timezone datetimes the inline views used to hand the
        # conflictList template.
        conflictStart = datetime.datetime(2030, 7, 1, 23, 0, tzinfo=datetime.UTC)
        conflictEnd = datetime.datetime(2030, 7, 2, 0, 30, tzinfo=datetime.UTC)
        conflict = EventAutomationDriver.Conflict(
            type=EventAutomationDriver.Conflict.ConflictType.GCAL,
            title="Tenant union mixer",
            start=conflictStart, end=conflictEnd, zoomUser=None,
        )
        job = PublishJob(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.CONFLICT,
            payload={}, conflicts=tasks._serializeConflicts([conflict], "America/Chicago"),
        )
        rendered = job.getResultContext()["conflicts"][0]
        self.assertEqual(rendered["start"], conflictStart.astimezone(CHICAGO).replace(tzinfo=None))
        self.assertEqual(rendered["end"], conflictEnd.astimezone(CHICAGO).replace(tzinfo=None))
        self.assertIsNone(rendered["start"].tzinfo)
        self.assertIsNone(rendered["end"].tzinfo)
        self.assertEqual(rendered["type"], EventAutomationDriver.Conflict.ConflictType.GCAL)
        self.assertEqual(rendered["title"], "Tenant union mixer")
        self.assertIsNone(rendered["zoomUser"])

    def test_unresolveable_context_uses_the_same_conflict_shape(self):
        conflict = EventAutomationDriver.Conflict(
            type=EventAutomationDriver.Conflict.ConflictType.ZOOM,
            title="Standing meeting",
            start=datetime.datetime(2030, 7, 1, 23, 0, tzinfo=datetime.UTC),
            end=datetime.datetime(2030, 7, 2, 0, 0, tzinfo=datetime.UTC),
            zoomUser="busy@austindsa.org",
        )
        job = PublishJob(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.UNRESOLVEABLE,
            payload={}, conflicts=tasks._serializeConflicts([conflict], "America/Chicago"),
        )
        rendered = job.getResultContext()["conflicts"][0]
        self.assertEqual(rendered["type"], EventAutomationDriver.Conflict.ConflictType.ZOOM)
        self.assertEqual(rendered["zoomUser"], "busy@austindsa.org")

    def test_failed_context_carries_the_error(self):
        job = PublishJob(
            kind=PublishJob.Kind.DIRECT, status=PublishJob.Status.FAILED,
            payload={}, errorMessage="Traceback: kaboom",
        )
        self.assertEqual(job.getResultContext(), {"errorStr": "Traceback: kaboom"})

    def test_non_terminal_context_is_empty(self):
        for status in (PublishJob.Status.PENDING, PublishJob.Status.RUNNING):
            with self.subTest(status=status):
                job = PublishJob(kind=PublishJob.Kind.DIRECT, status=status, payload={})
                self.assertEqual(job.getResultContext(), {})
