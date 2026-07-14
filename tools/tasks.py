"""Huey background tasks for the tools app.

huey.contrib.djhuey autodiscovers this module (tasks.py in each installed
app), so tasks defined here are registered on both the web process (for
enqueueing) and the consumer (`manage.py run_huey` - the `worker` service in
docker-compose.yml).

In dev and under tests Huey runs in "immediate" mode (see HUEY in
settings.py): tasks execute inline and periodic schedules do NOT fire - run
the underlying management commands manually instead.
"""

import datetime
import logging
import time
import traceback

import pytz
from django.core.management import call_command
from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task

import settings

from .EmailApi import EmailApi
from .EventAutomation import EventAutomationDriver
from .SecretManager import SecretManager
from .models import DelegatedEvents, PostedEvents, PublishJob, User

logger = logging.getLogger(__name__)


# Crontab times use the consumer's clock, which is UTC in the containers
# (never give a container a TZ - it silently shifts published event times).
# 11:00 UTC is 5/6am Central: refreshed before the workday, after any
# late-night wiki edits.
@db_periodic_task(crontab(hour="11", minute="0"))
def syncLinkTreeWiki():
    """Daily wiki-link resolution for Link Tree WIKI items.

    The management command stays the imperative core (manual runs and
    --dry-run keep working); this is just its schedule.
    """
    # The command raises SystemExit(1) when items errored so host schedulers
    # see a non-zero exit; inside the consumer that must become a logged
    # failure, not an exit attempt.
    try:
        call_command("sync_link_tree_wiki", quiet=True)
    except SystemExit as e:
        if e.code:
            logger.error(
                "sync_link_tree_wiki reported errors (exit code %s)", e.code
            )


# National sends the membership list monthly, but a daily poll is cheap and
# fully idempotent (MembershipSnapshot.listDate is unique; the email fetch
# never marks anything read) - daily means a fresh list shows up in the
# bleeding curve within a day of arriving instead of waiting on a weekly/
# monthly schedule, with no real cost since most days it just finds nothing
# new. 12:00 UTC (staggered an hour after syncLinkTreeWiki's 11:00) is 6/7am
# Central - well before the workday.
@db_periodic_task(crontab(hour="12", minute="0"))
def ingestMembershipLists():
    """Daily poll of the austindsalistbot inbox for new membership lists.

    The management command stays the imperative core (manual runs, --dry-run,
    and the --from-dir backfill path all keep working); this is just its
    schedule. Gracefully no-ops (exit 0) until the inbox credentials are
    configured (SecretManager.getMembershipBotEmailConfig()).
    """
    try:
        call_command("ingest_membership_lists", quiet=True)
    except SystemExit as e:
        if e.code:
            logger.error(
                "ingest_membership_lists reported errors (exit code %s)", e.code
            )


# --- Event publishing (PublishJob) ------------------------------------------
#
# The two real-publish flows in eventViews.py (new_event and the
# approve_delegated_event approve branch) validate synchronously, create a
# PublishJob row carrying the serialized EventInfo, enqueue publishEventJob,
# and redirect to a polling status page. The job row is the only source of
# truth for the run (HUEY["results"] is False).


def _rehydrateEventInfo(payload: dict) -> EventAutomationDriver.EventInfo:
    """Rebuild the EventInfo serialized by eventViews._buildEventPayload.

    startIso/endIso are tz-aware isoformat strings (already localized by the
    form/model at enqueue time); fromisoformat() yields aware fixed-offset
    datetimes, which satisfies the driver's aware-input guard without
    re-localizing."""
    return EventAutomationDriver.EventInfo(
        title=payload["title"],
        eventType=payload["eventType"],
        start=datetime.datetime.fromisoformat(payload["startIso"]),
        end=datetime.datetime.fromisoformat(payload["endIso"]),
        locationName=payload["locationName"],
        streetAddress=payload["streetAddress"],
        city=payload["city"],
        state=payload["state"],
        zip=payload["zip"],
        description=payload["description"],
        instructions=payload["instructions"],
        country=payload["country"],
        zoomRequired=payload["zoomRequired"],
    )


def _serializeConflicts(conflicts, timezoneStr: str) -> list:
    """Conflict times go into the job row as NAIVE ISO strings localized to the
    payload timezone - exactly the localize-then-strip the views used to do
    inline before rendering, so getResultContext() can hand conflictList.html
    the same naive datetimes it has always rendered."""
    timezone = pytz.timezone(timezoneStr)
    serialized = []
    for conflict in conflicts:
        start = conflict.start.astimezone(timezone).replace(tzinfo=None)
        end = conflict.end.astimezone(timezone).replace(tzinfo=None)
        serialized.append({
            "type": conflict.type,
            "title": conflict.title,
            "zoomUser": conflict.zoomUser,
            "startIso": start.isoformat(),
            "endIso": end.isoformat(),
        })
    return serialized


def _finishDirectPublish(job: PublishJob, eventInfo, result) -> None:
    """Persist a successful DIRECT publish: the PostedEvents row and the
    confirmation email, mirroring what new_event used to do inline."""
    # Convert event start and end dates to utc
    utcStart = eventInfo.start.astimezone(pytz.utc)
    utcEnd = eventInfo.end.astimezone(pytz.utc)
    utcNow = datetime.datetime.now(datetime.UTC)
    e = PostedEvents.objects.create(title = eventInfo.title,
                                    start = utcStart,
                                    end = utcEnd,
                                    timezone = job.payload["timezone"],
                                    locationName = eventInfo.locationName,
                                    streetAddress = eventInfo.streetAddress,
                                    city = eventInfo.city,
                                    state = eventInfo.state,
                                    zip = eventInfo.zip,
                                    country = eventInfo.country,
                                    description = eventInfo.description,
                                    instructions = eventInfo.instructions,
                                    dateCreated = utcNow,
                                    datePublished = utcNow,
                                    anManageLink = result.anManageLink if result.anManageLink is not None else "",
                                    anShareLink = result.anShareLink if result.anShareLink is not None else "",
                                    gCalLink = result.gCalLink if result.gCalLink is not None else "",
                                    zoomLink = result.zoomLink if result.zoomLink is not None else "",
                                    zoomAccount = result.zoomAccount if result.zoomAccount is not None else "",
                                    zoomRequired = eventInfo.zoomRequired,
                                    creator = job.creator,
                                    authorizer = job.creator,
                                    owner = job.owner,
                                    reason = "Created by approved authorizer")
    job.postedEvent = e

    # Send email
    # TODO: SMTP email is broken
    try:
        messageText = f""" Your event {eventInfo.title} was published successfully. Here are the links.
        Zoom Link ({result.zoomAccount}): {result.zoomLink}
        AN Share Link: {result.anShareLink}
        AN Manage Link: {result.anManageLink}
        Google Calendar Link: {result.gCalLink}"""
        EmailApi.sendEmailFromWebsiteAccount(
            toAddress=job.creator.email,
            subject=f"Published {eventInfo.title} event succesfully",
            messageText=messageText,
        )
    except Exception as err:
        logger.error(
            "PublishEventJob: Failed to send confrimation email due to exception"
        )
        logger.exception(err)


def _finishDelegatedPublish(job: PublishJob, eventInfo, result) -> None:
    """Persist a successful DELEGATED publish: flip the DelegatedEvents row to
    APPROVED BEFORE creating the PostedEvents row (today's ordering - keep it),
    then email the approver and the requester."""
    event = job.delegatedEvent
    approver = User.objects.get(id=job.payload["approverId"])
    reason = job.payload["reason"]
    utcNow = datetime.datetime.now(datetime.UTC)
    event.status = DelegatedEvents.Status.APPROVED
    event.approver = approver
    event.dateReviewed = utcNow
    event.reason = reason
    event.save()
    e = PostedEvents.objects.create(title = eventInfo.title,
                                    start = event.start,
                                    end = event.end,
                                    timezone = event.timezone,
                                    locationName = event.locationName,
                                    streetAddress = event.streetAddress,
                                    city = event.city,
                                    state = event.state,
                                    zip = event.zip,
                                    country = event.country,
                                    description = event.description,
                                    instructions = event.instructions,
                                    dateCreated = utcNow,
                                    datePublished = utcNow,
                                    anManageLink = result.anManageLink if result.anManageLink is not None else "",
                                    anShareLink = result.anShareLink if result.anShareLink is not None else "",
                                    gCalLink = result.gCalLink if result.gCalLink is not None else "",
                                    zoomLink = result.zoomLink if result.zoomLink is not None else "",
                                    zoomAccount = result.zoomAccount if result.zoomAccount is not None else "",
                                    zoomRequired = eventInfo.zoomRequired,
                                    creator = event.creator,
                                    authorizer = approver,
                                    owner = event.owner,
                                    reason = reason)
    job.postedEvent = e

    # Send email
    try:
        messageText = f""" Your event {eventInfo.title} was approved by {approver.getUserNameString()} published successfully. Here are the links.
        Zoom Link ({result.zoomAccount}): {result.zoomLink}
        AN Share Link: {result.anShareLink}
        AN Manage Link: {result.anManageLink}
        Google Calendar Link: {result.gCalLink}"""
        EmailApi.sendEmailFromWebsiteAccount(
            toAddress=approver.email,
            subject=f"Published {eventInfo.title} event succesfully",
            messageText=messageText,
        )
        EmailApi.sendEmailFromWebsiteAccount(
            toAddress=event.creator.email,
            subject=f"Published {eventInfo.title} event succesfully",
            messageText=messageText,
        )
    except Exception as err:
        logger.error(
            "PublishEventJob: Failed to send confrimation email due to exception"
        )
        logger.exception(err)


# retries=0 is load-bearing: Action Network has no delete API, so a retry
# after a partial run can double-publish an event nobody can programmatically
# remove. Failures land in the job row for a human to triage instead.
@db_task(retries=0)
def publishEventJob(jobId):
    """Run one PublishJob end to end: rehydrate the EventInfo, publish through
    EventAutomationDriver, persist the outcome (PostedEvents / conflicts /
    error) back onto the job row for the polling status page."""
    try:
        job = PublishJob.objects.get(id=jobId)
    except PublishJob.DoesNotExist:
        logger.error("PublishEventJob: PublishJob %s does not exist, nothing to do", jobId)
        return
    job.status = PublishJob.Status.RUNNING
    job.startedAt = datetime.datetime.now(datetime.UTC)
    job.save()
    try:
        payload = job.payload
        # Refuse a schema we don't understand - failing loudly here beats
        # publishing garbage from a payload written by different code.
        if payload.get("payloadVersion") != PublishJob.PAYLOAD_VERSION:
            raise Exception(
                f"PublishEventJob: Unsupported payload version {payload.get('payloadVersion')!r} "
                f"(expected {PublishJob.PAYLOAD_VERSION})"
            )
        eventInfo = _rehydrateEventInfo(payload)

        if settings.DEMO_MODE:
            # The demo box has stubbed Zoom / Action Network / Google credentials
            # and no Selenium container, so a real publish can only fail. Skip it
            # and return a placeholder PUBLISHED result so the demo walks the full
            # happy path (spinner -> success page with links) without contacting
            # any external service or creating a real meeting. The short sleep
            # makes the spinner state visible; the real pipeline takes 15-30s.
            # Deliberately covers BOTH kinds: the old inline approve flow had no
            # demo path and would attempt (and fail) a real publish on the demo
            # box - stubbing both is strictly better for the demo.
            logger.info("PublishEventJob: DEMO_MODE is on, returning a stubbed result without publishing")
            time.sleep(2)
            result = EventAutomationDriver.Result(
                type=EventAutomationDriver.Result.ResultType.PUBLISHED,
                zoomAccount="events@austindsa.org (demo)" if eventInfo.zoomRequired else "",
                zoomLink="https://us02web.zoom.us/j/0000000000" if eventInfo.zoomRequired else "",
                anManageLink="https://actionnetwork.org/events/demo-event/manage",
                anShareLink="https://actionnetwork.org/events/demo-event",
                gCalLink="https://calendar.google.com/calendar/u/0/r/eventedit",
            )
        else:
            logger.info("PublishEventJob: Attempting to publish event for job %s", jobId)
            result = EventAutomationDriver.publishEvent(
                eventInfo=eventInfo,
                config=EventAutomationDriver.Config(
                    zoomConfig=SecretManager.getZoomConfig(),
                    anConfig=SecretManager.getANAutomatorConfig(),
                    gCalConfig=SecretManager.getGCalConfig(),
                    ignoreResolveableConflicts=payload["ignoreResolveableConflicts"],
                ),
            )

        if result.type == EventAutomationDriver.Result.ResultType.PUBLISHED:
            logger.info("PublishEventJob: Event published successfully with result %s", str(result))
            if job.kind == PublishJob.Kind.DELEGATED:
                _finishDelegatedPublish(job, eventInfo, result)
            else:
                _finishDirectPublish(job, eventInfo, result)
            job.status = PublishJob.Status.PUBLISHED
        elif result.type == EventAutomationDriver.Result.ResultType.UNRESOLVEABLE_CONFLICT:
            logger.info("PublishEventJob: Publish failed with unresolveable conflicts %s", str(result))
            job.conflicts = _serializeConflicts(result.conflicts, payload["timezone"])
            job.status = PublishJob.Status.UNRESOLVEABLE
        elif result.type == EventAutomationDriver.Result.ResultType.CONFLICT:
            logger.info("PublishEventJob: Publish failed with resolveable conflicts %s", str(result))
            job.conflicts = _serializeConflicts(result.conflicts, payload["timezone"])
            job.status = PublishJob.Status.CONFLICT
        else:
            logger.error("PublishEventJob: Unexpected error when publishing event %s", str(result))
            job.errorMessage = "".join(result.errorStr or [])
            job.status = PublishJob.Status.FAILED
    except Exception:
        logger.exception("PublishEventJob: Unexpected exception publishing job %s", jobId)
        job.status = PublishJob.Status.FAILED
        job.errorMessage = traceback.format_exc()
    job.finishedAt = datetime.datetime.now(datetime.UTC)
    job.save()
