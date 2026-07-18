import datetime
import logging
import dataclasses
import pytz

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden, HttpResponseBadRequest, JsonResponse
from django.contrib.auth.decorators import permission_required, login_required
from django.contrib.auth.models import User
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin

from .EventAutomation import EventAutomationDriver
from .SecretManager import SecretManager
from .forms import NewEventForm, ApproveDelegatedEventForm
from .EmailApi import EmailApi
from .permissions import *
from .tasks import publishEventJob
from .timezones import DateTimeWithAcceptedTimeZone
import dataclasses
from .models import *

logger = logging.getLogger(__name__)

# MARK: Detail and List View

class DelegatedEventDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = VIEW_DELEGATED_EVENTS
    model = DelegatedEvents
    template_name = "tools/delegated-events/details.html"

class DelegatedEventListView(LoginRequiredMixin, PermissionRequiredMixin,ListView):
    permission_required = VIEW_DELEGATED_EVENTS
    model = DelegatedEvents
    template_name = "tools/delegated-events/list.html"

    def get_context_data(self, **kwargs):
        # The approve page layers two checks this list's permission doesn't
        # imply (APPROVE_DELEGATED_EVENT + owner.authorizers), so pick each
        # row's link per-viewer: approvers get the approve page, everyone
        # else the read-only detail page.
        context = super().get_context_data(**kwargs)
        for event in context["object_list"]:
            event.viewerUrl = event.getUrlFor(self.request.user)
        return context

class PostedEventDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = VIEW_PUBLISHED_EVENTS
    model = PostedEvents
    template_name = "tools/events/details.html"

class PostedEventListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = VIEW_PUBLISHED_EVENTS
    model = PostedEvents
    template_name = "tools/events/list.html"

# MARK: Publish New Event

def _buildEventPayload(eventInfo, ignoreResolveableConflicts) -> dict:
    """Serialize an EventInfo into the PublishJob payload (schema
    PublishJob.PAYLOAD_VERSION).

    start/end are stored as the literal LOCAL WALL time (naive ISO), with the
    accepted IANA zone carried once in "timezone". The datetime's own tzinfo is
    deliberately NOT used to convey the zone: isoformat() would preserve only
    the offset, and tasks._rehydrateEventInfo would get back a fixed-offset
    tzinfo with no zone name (the issue #26 regression). DateTimeWithAcceptedTimeZone
    keeps the wall time and the zone name as the two explicit facts they are, so
    the round trip is lossless and stores exactly what the user entered. start/end
    are already localized here (by convertToEventInfo / getEventInfo)."""
    return {
        "payloadVersion": PublishJob.PAYLOAD_VERSION,
        "title": eventInfo.title,
        "eventType": eventInfo.eventType,
        "timezone": eventInfo.start.zoneName,
        "startIso": eventInfo.start.wallIso(),
        "endIso": eventInfo.end.wallIso(),
        "locationName": eventInfo.locationName,
        "streetAddress": eventInfo.streetAddress,
        "city": eventInfo.city,
        "state": eventInfo.state,
        "zip": str(eventInfo.zip),
        "country": eventInfo.country,
        "description": eventInfo.description,
        "instructions": eventInfo.instructions,
        "zoomRequired": eventInfo.zoomRequired,
        "ignoreResolveableConflicts": ignoreResolveableConflicts,
    }


@login_required
@permission_required(PUBLISH_EVENT)
def new_event(request):
    if request.method == "POST":
        logger.info("PublishEvent: Recieved submission of event to publish.")
        form = NewEventForm(request.POST)
        if not form.is_valid():
            logger.error("PublishEvent: Submitted Form is not valid")
            return render(request, "tools/new-event/unknown.html", {"errorStr": "The form could not be validated, please go back and try again."})

        eventInfo = form.convertToEventInfo()
        logger.info("PublishEvent: Recieved event data %s", str(eventInfo))

        logger.info("PublishEvent: Getting Owner")
        formOwner = form.cleaned_data[NewEventForm.Keys.OWNER]
        try:
            owner = EventOwners.objects.get(name = formOwner)
        except Exception as err:
            logger.exception("PublishEvent: Could not get event owner")
            raise err
        
        # isActive is a method - the call parens matter. Reading the bare
        # attribute (the old bug) tests the bound method, which is always
        # truthy, silently disabling the expiration check.
        if not owner.isActive():
            logger.error("PublishEvent: Owner %s is no longer active and cannot create events", owner.name)
            return render(request, "tools/new-event/unknown.html", {"errorStr": f"Owner {owner.name} is no longer active and cannot create events"})
        if request.user not in owner.authorizers.all():
            logger.error("PublishEvent: You are not an authorizer for owner %s", owner.name)
            return render(request, "tools/new-event/unknown.html", {"errorStr": f"You are not an authorizer for owner {owner.name}"})
        
        logger.info("PublishEvent: Got and validated event owner %s", owner.name)

        ignoreResolveableConflicts = form.cleaned_data[
            NewEventForm.Keys.IGNORE_RESOLVEABLE_CONFLICTS
        ]

        # The publish itself runs in the Huey worker (it takes 15-30s and will
        # drive Selenium); the POST just records a PublishJob and bounces to a
        # polling status page. The result therefore lives at a GET-able URL -
        # refresh and back-button safe, no form re-post.
        job = PublishJob.objects.create(
            kind=PublishJob.Kind.DIRECT,
            payload=_buildEventPayload(
                eventInfo,
                ignoreResolveableConflicts,
            ),
            creator=request.user,
            owner=owner,
        )
        publishEventJob(job.id)
        logger.info("PublishEvent: Enqueued publish job %s for event %s", job.id, eventInfo.title)
        return redirect("publish-status", jobId=job.id)
    else:
        form = NewEventForm(
            initial={
                "startTime": datetime.datetime.now(),
                "endTime": datetime.datetime.now(),
            }
        )
        return render(request, "tools/new-event/new.html", {"form": form})

# MARK: Delegated Events

@login_required
@permission_required(REQUEST_DELEGATED_EVENT)
def new_delegated_event(request):
    if request.method == "POST":
        logger.info("PublishDelegatedEvent: Recieved submission of event to publish.")
        form = NewEventForm(request.POST)
        if not form.is_valid():
            logger.error("PublishDelegatedEvent: Submitted Form is not valid")
            # error.html, not unknown.html - unknown.html doesn't exist in
            # this template directory, and this branch is reachable now that
            # the owner dropdown excludes unhealthy owners (a stale tab can
            # submit an owner that has since expired or lost its authorizers).
            return render(request, "tools/new-delegated-event/error.html", {"errorStr": "The form could not be validated, please go back and try again."})

        eventInfo = form.convertToEventInfo()
        logger.info("PublishDelegatedEvent: Recieved event data %s", str(eventInfo))

        logger.info("PublishDelegatedEvent: Getting Owner")
        formOwner = form.cleaned_data[NewEventForm.Keys.OWNER]
        try:
            owner = EventOwners.objects.get(name = formOwner)
        except Exception as err:
            logger.exception("PublishDelegatedEvent: Could not get event owner")
            raise err

        # Inactive owners can't receive new requests. Sits before the
        # publishEvent dry-run below so we never touch Zoom/AN/gCal for a
        # request that can't proceed. (The error.html context here is
        # deliberately {"errorStr": ...} - the template reads only errorStr;
        # the unexpected-error branch below passes dataclasses.asdict(result),
        # which has no errorStr key and renders a blank message. Don't "align"
        # this to that pre-existing bug.)
        if not owner.isActive():
            logger.error("PublishDelegatedEvent: Rejected delegated event request for inactive owner %s", owner.name)
            return render(request, "tools/new-delegated-event/error.html",
                          {"errorStr": f"Owner {owner.name} is no longer active and cannot accept event requests"})
        logger.info("PublishDelegatedEvent: Got and validated event owner %s", owner.name)
        
        logger.info("PublishDelegatedEvent: Getting configuration")
        zoomConfig = SecretManager.getZoomConfig()
        anConfig = SecretManager.getANAutomatorConfig()
        gCalConfig = SecretManager.getGCalConfig()

        ignoreResolveableConflicts = form.cleaned_data[
            NewEventForm.Keys.IGNORE_RESOLVEABLE_CONFLICTS
        ]

        logger.info("PublishDelegatedEvent: Check for conflicts")
        result = EventAutomationDriver.publishEvent(
            eventInfo=eventInfo,
            config=EventAutomationDriver.Config(
                zoomConfig=zoomConfig,
                anConfig=anConfig,
                gCalConfig=gCalConfig,
                ignoreResolveableConflicts=ignoreResolveableConflicts,
                onlyCheckConflicts=True
            )
        )
        if result.type == EventAutomationDriver.Result.ResultType.NO_CONFLICTS:
            logger.info("PublishDelegatedEvent: Event Request has no conflicts. Creating request for %s", eventInfo.title)
            # Convert event start and end dates to utc
            utcStart = eventInfo.start.utc()
            utcEnd = eventInfo.end.utc()
            utcNow = datetime.datetime.now(datetime.UTC)
            e = DelegatedEvents.objects.create(title = eventInfo.title,
                                               start = utcStart,
                                               end = utcEnd,
                                               timezone = form.cleaned_data[NewEventForm.Keys.TIMEZONE],
                                               locationName = eventInfo.locationName,
                                               streetAddress = eventInfo.streetAddress,
                                               city = eventInfo.city,
                                               state = eventInfo.state,
                                               zip = eventInfo.zip,
                                               country = eventInfo.country,
                                               description = eventInfo.description,
                                               instructions = eventInfo.instructions,
                                               dateCreated = utcNow,
                                               creator = request.user,
                                               owner = owner,
                                               zoomRequired = eventInfo.zoomRequired,
                                               status = DelegatedEvents.Status.REQUESTED)
            e.save()
            # Email authorizers that a new event has been requested
            try:
                # TODO: potentially replace with Django built in mail module
                messageText = f"""
                A new event request has been created for {owner.name}, of which you are an authorized approver.
                Please visit {request.build_absolute_uri(reverse("approve-delegated-event", kwargs={ "id" :e.id}))} to either approve or reject the event.
                """
                for approver in owner.authorizers.all():
                    EmailApi.sendEmailFromWebsiteAccount(
                        toAddress=approver.email,
                        subject=f"Event {eventInfo.title} has been requested",
                        messageText=messageText
                    )
                messageText = f"""
                Your event request for {eventInfo.title} has been created and sent to {owner.name}. You will recieve an email when it has been approved.
                """
                EmailApi.sendEmailFromWebsiteAccount(
                    toAddress=request.user.email,
                    subject="Event Request Created",
                    messageText=messageText
                )
            except Exception as err:
                logger.error(
                    "PublishDelegatedEvent: Failed to send authorization emails"
                )
                logger.exception(err)
            return render(request, "tools/new-delegated-event/created.html", {"owner": owner.name})

        elif result.type == EventAutomationDriver.Result.ResultType.UNRESOLVEABLE_CONFLICT:
            # Let the user know about the unresolveable conflict
            logger.info(
                "PublishDelegatedEvent: Event Request Creation Failed with Unresolveable Conflict %s",
                str(result),
            )
            # Convert conflict times to timezone specified in form, then make naiive
            return render(
                request,
                "tools/new-delegated-event/unresolveable.html",
                dataclasses.asdict(result),
            )
        elif result.type == EventAutomationDriver.Result.ResultType.CONFLICT:
            # Let the user know about the conflict and ask if they want to ignore it
            logger.info(
                "PublishDelegatedEvent: Event Request Failed with Unresolveable Conflict %s",
                str(result),
            )
            return render(
                request, "tools/new-delegated-event/resolveable.html", dataclasses.asdict(result)
            )
        else:
            # Some unkown error occured, show the user all informaiton we have
            logger.error(
                "PublishDelegatedEvent: Unexpected error when creating event request %s", str(result)
            )
            return render(
                request, "tools/new-delegated-event/error.html", dataclasses.asdict(result)
            )
    else:
        form = NewEventForm(
            initial={
                "startTime": datetime.datetime.now(),
                "endTime": datetime.datetime.now(),
            }
        )
        return render(request, "tools/new-delegated-event/new.html", {"form": form})
    
@login_required
@permission_required(APPROVE_DELEGATED_EVENT)
def approve_delegated_event(request, id):
    # Check if the delegated event exists
    event = None
    try:
        event = DelegatedEvents.objects.get(id=id)
    except Exception as err:
        logger.exception("ApproveDelegatedEvent: Could not retrieve delegated event request %s due to unexpected error %s", id, err)
        return render(request, "tools/approve-delegated-event/error.html", {"errorStr" : str(err)})
    
    # Check if the event had already been approved/denied, if so redirect to the page that views a delegated event
    if event.status != DelegatedEvents.Status.REQUESTED:
        logger.info("ApproveDelegatedEvent: Event %s is already approved or denied, redirecting to detail view", str(id))
        return redirect("delegated-event-detail", pk=id)
    
    # Check if the current user is allowed to approve
    if request.user not in event.owner.authorizers.all():
        logger.error("ApproveDelegatedEvent: User %s does not have authorization to approve event %s for owner %s", request.user.email, str(event.id), event.owner.name)
        return render(request, "tools/approve-delegated-event/unauthorized.html", {"owner" : event.owner.name})

    if request.method == "POST":
        # Process approve/disapprove
        form  = ApproveDelegatedEventForm(request.POST)
        if not form.is_valid():
            logger.error("ApproveDelegatedEvent: Submitted Form is not valid")
            return render(request, "tools/approve-delegated-event/error.html", {"errorStr": "The form could not be validated, please go back and try again."})
        formData = form.cleaned_data
        if formData[ApproveDelegatedEventForm.Keys.APPROVE] == "YES":
            logger.info("ApprovedDelegatedEvent: Authorizer %s approved the event %d", request.user.getUserNameString(), id)
            eventInfo = event.getEventInfo()
            if not eventInfo:
                logger.error("ApprovedDelegateEvent: EventInfo for %d could not be created", id)
                return render(request, "tools/approve-delegated-event/unknown.html", {"errorStr": "Could not create the event info for creation."}) 
            # The publish runs in the Huey worker, same as new_event. The
            # request row only flips to APPROVED when the worker actually
            # publishes (tasks._finishDelegatedPublish), so a failed publish
            # leaves the request reviewable. The approve flow always forces
            # ignoreResolveableConflicts - the requester's dry run already
            # surfaced gCal conflicts at request time.
            logger.info("ApprovedDelegateEvent: Enqueueing publish job for event %d", id)
            payload = _buildEventPayload(eventInfo, ignoreResolveableConflicts=True)
            payload["reason"] = formData[ApproveDelegatedEventForm.Keys.REASON]
            payload["approverId"] = request.user.id
            job = PublishJob.objects.create(
                kind=PublishJob.Kind.DELEGATED,
                payload=payload,
                creator=request.user,
                owner=event.owner,
                delegatedEvent=event,
            )
            publishEventJob(job.id)
            return redirect("publish-status", jobId=job.id)
        else:
            logger.info("ApprovedDelegatedEvent: Authorizer %s denied the event %d", request.user.getUserNameString(), id)
            event.status = DelegatedEvents.Status.DENIED
            event.approver = request.user
            event.dateReviewed = datetime.datetime.now(datetime.UTC)
            event.reason = formData[ApproveDelegatedEventForm.Keys.REASON]
            event.save()
             # Send email
            try:
                # TODO: potentially replace with Django built in mail module
                messageText = f""" Your event {event.title} was denied by {request.user.getUserNameString()}.
                Reason: {formData[ApproveDelegatedEventForm.Keys.REASON]}"""
                EmailApi.sendEmailFromWebsiteAccount(
                    toAddress=event.creator.email,
                    subject=f"{event.title} was denied",
                    messageText=messageText,
                )
            except Exception as err:
                logger.error(
                    "ApprovedDelegateEvent: Failed to send confrimation email due to exception"
                )
                logger.exception(err)
            return redirect("delegated-event-detail", pk=id)
    else:
        form = ApproveDelegatedEventForm()
        return render(request, "tools/approve-delegated-event/approve.html", {"form": form, "object":DelegatedEvents.objects.get(id=id)})

# MARK: Publish Status

def _canViewJob(user, job) -> bool:
    # The job carries form input and result links for one person's publish -
    # only its creator (or a superuser) gets to watch it.
    return user.is_superuser or (job.creator_id is not None and job.creator_id == user.id)


@login_required
def publish_status(request, jobId):
    """One URL for the whole publish: the spinner while the job is queued or
    running, then (on reload, triggered by the poll script) the SAME existing
    result template the inline flow used to render, fed from the job row."""
    job = get_object_or_404(PublishJob, id=jobId)
    if not _canViewJob(request.user, job):
        return HttpResponseForbidden("You do not have access to this publish job.")
    if not job.isTerminal():
        return render(request, "tools/publish-status/status.html", {"job": job})
    if job.status == PublishJob.Status.PUBLISHED:
        if job.kind == PublishJob.Kind.DELEGATED:
            # Land where the inline approve flow always landed.
            return redirect("delegated-event-detail", pk=job.delegatedEvent_id)
        return render(request, "tools/new-event/published.html", job.getResultContext())
    if job.status == PublishJob.Status.CONFLICT:
        if job.kind == PublishJob.Kind.DELEGATED:
            # Unreachable: the approve flow forces ignoreResolveableConflicts,
            # so the driver never returns CONFLICT for it. Defensive render.
            return render(request, "tools/approve-delegated-event/unresolveable.html", job.getResultContext())
        context = job.getResultContext()
        context["job"] = job  # resolveable.html links the publish-anyway form to this job
        return render(request, "tools/new-event/resolveable.html", context)
    if job.status == PublishJob.Status.UNRESOLVEABLE:
        template = ("tools/approve-delegated-event/unresolveable.html"
                    if job.kind == PublishJob.Kind.DELEGATED
                    else "tools/new-event/unresolveable.html")
        return render(request, template, job.getResultContext())
    # FAILED
    template = ("tools/approve-delegated-event/unknown.html"
                if job.kind == PublishJob.Kind.DELEGATED
                else "tools/new-event/unknown.html")
    return render(request, template, job.getResultContext())


@login_required
def publish_status_json(request, jobId):
    """The poll endpoint behind the spinner page. The response shape
    (status / statusLabel / isTerminal / createdAtIso) is the documented
    contract for job-status polling - future polled jobs (e.g. vote
    validation) should reuse the shape, not this code."""
    job = get_object_or_404(PublishJob, id=jobId)
    if not _canViewJob(request.user, job):
        return HttpResponseForbidden("You do not have access to this publish job.")
    return JsonResponse({
        "status": job.status,
        "statusLabel": job.getStatusAsString(),
        "isTerminal": job.isTerminal(),
        "createdAtIso": job.createdAt.isoformat(),
    })


def _findRecentSiblingJob(job):
    """The server-side double-publish guard for publish_anyway (the JS
    button-disable is best-effort only): another job by the same creator for
    the same event identity (payload title + startIso), created in the last
    10 minutes, that is pending, running, or already published. FAILED
    siblings don't count - a legitimate retry after a failure must work."""
    blockingStatuses = (
        PublishJob.Status.PENDING,
        PublishJob.Status.RUNNING,
        PublishJob.Status.PUBLISHED,
    )
    windowStart = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
    recentJobs = (PublishJob.objects
                  .filter(creator=job.creator, createdAt__gte=windowStart)
                  .exclude(id=job.id))
    # Python-side payload matching on purpose: JSONField path lookups are
    # shaky on SQLite and the table is tiny.
    for sibling in recentJobs:
        if sibling.status not in blockingStatuses:
            continue
        siblingPayload = sibling.payload or {}
        if (siblingPayload.get("title") == job.payload.get("title")
                and siblingPayload.get("startIso") == job.payload.get("startIso")):
            return sibling
    return None


@login_required
@require_POST
def publish_anyway(request, jobId):
    """Force-publish past a resolveable (gCal) conflict: clone the CONFLICT
    job with ignoreResolveableConflicts on and enqueue the clone. Direct
    publishes only - the approve flow already forces the flag."""
    job = get_object_or_404(PublishJob, id=jobId)
    if not _canViewJob(request.user, job):
        return HttpResponseForbidden("You do not have access to this publish job.")
    if job.kind != PublishJob.Kind.DIRECT or job.status != PublishJob.Status.CONFLICT:
        return HttpResponseBadRequest(
            "Only a direct publish stopped by a calendar conflict can be published anyway."
        )
    sibling = _findRecentSiblingJob(job)
    if sibling is not None:
        # A same-event job is already in flight (or just published) - don't
        # start another; show the user the one that exists.
        logger.info("PublishAnyway: Rejected duplicate publish of job %s, sibling job %s exists", job.id, sibling.id)
        return redirect("publish-status", jobId=sibling.id)
    newJob = PublishJob.objects.create(
        kind=job.kind,
        payload={**job.payload, "ignoreResolveableConflicts": True},
        creator=job.creator,
        owner=job.owner,
    )
    publishEventJob(newJob.id)
    logger.info("PublishAnyway: Cloned conflict job %s into job %s with ignoreResolveableConflicts on", job.id, newJob.id)
    return redirect("publish-status", jobId=newJob.id)