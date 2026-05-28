import datetime
import time
import logging
import dataclasses
import pytz

from django.shortcuts import render, redirect
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import permission_required, login_required
from django.contrib.auth.models import User
from django.urls import reverse
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin

from .EventAutomation import EventAutomationDriver
from .SecretManager import SecretManager
from .forms import NewEventForm, ApproveDelegatedEventForm
from .EmailApi import EmailApi
from .permissions import *
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

class PostedEventDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = VIEW_PUBLISHED_EVENTS
    model = PostedEvents
    template_name = "tools/events/details.html"

class PostedEventListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = VIEW_PUBLISHED_EVENTS
    model = PostedEvents
    template_name = "tools/events/list.html"

# MARK: Publish New Event

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
        
        if not owner.isActive:
            logger.error("PublishEvent: Owner %s is no longer active and cannot create events", owner.name)
            return render(request, "tools/new-event/unknown.html", {"errorStr": f"Owner {owner.name} is no longer active and cannot create"})
        if request.user not in owner.authorizers.all():
            logger.error("PublishEvent: You are not an authorizer for owner %s", owner.name)
            return render(request, "tools/new-event/unknown.html", {"errorStr": f"You are not an authorizer for owner {owner.name}"})
        
        logger.info("PublishEvent: Got and validated event owner %s", owner.name)
        
        logger.info("PublishEvent: Getting configuration")
        zoomConfig = SecretManager.getZoomConfig()
        anConfig = SecretManager.getANAutomatorConfig()
        gCalConfig = SecretManager.getGCalConfig()

        ignoreResolveableConflicts = form.cleaned_data[
            NewEventForm.Keys.IGNORE_RESOLVEABLE_CONFLICTS
        ]

        logger.info("PublishEvent: Attempting to publish event")
        result = EventAutomationDriver.publishEvent(
            eventInfo=eventInfo,
            config=EventAutomationDriver.Config(
                zoomConfig=zoomConfig,
                anConfig=anConfig,
                gCalConfig=gCalConfig,
                ignoreResolveableConflicts=ignoreResolveableConflicts
            )
        )
        # Left around for debugging, useful if you want to test thigns out without having to actuall publish a bunch of stuff
        # result = EventAutomationDriver.Result(EventAutomationDriver.Result.ResultType.PUBLISHED, anManageLink="manageLink", anShareLink="shareLink", gCalLink="gCalLink", zoomAccount="Account", zoomLink="zoomLink")
        if result.type == EventAutomationDriver.Result.ResultType.PUBLISHED:
            # Return success and links back to user
            logger.info(
                "PublishEvent: Event Publish sucessfully with result %s", str(result)
            )
            logger.info(
                "PublishEvent: Sending confirmation email to user %s",
                str(request.user.email),
            )
            # Save event to database
            # Convert event start and end dates to utc
            utcStart = eventInfo.start.astimezone(pytz.utc)
            utcEnd = eventInfo.end.astimezone(pytz.utc)
            utcNow = datetime.datetime.now(datetime.UTC)
            e = PostedEvents.objects.create(title = eventInfo.title,
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
                                            datePublished = utcNow,
                                            anManageLink = result.anManageLink if result.anManageLink is not None else "",
                                            anShareLink = result.anShareLink if result.anShareLink is not None else "",
                                            gCalLink = result.gCalLink if result.gCalLink is not None else "",
                                            zoomLink = result.zoomLink if result.zoomLink is not None else "",
                                            zoomAccount = result.zoomAccount if result.zoomAccount is not None else "",
                                            zoomRequired = eventInfo.zoomRequired,
                                            creator = request.user,
                                            authorizer = request.user,
                                            owner = owner,
                                            reason = "Created by approved authorizer")
            e.save()

            # Send email
            # TODO: SMTP email is broken
            try:
                # TODO: potentially replace with Django built in mail module
                messageText = f""" Your event {eventInfo.title} was published successfully. Here are the links.
                Zoom Link ({result.zoomAccount}): {result.zoomLink}
                AN Share Link: {result.anShareLink}
                AN Manage Link: {result.anManageLink}
                Google Calendar Link: {result.gCalLink}"""
                EmailApi.sendEmailFromWebsiteAccount(
                    toAddress=request.user.email,
                    subject=f"Published {eventInfo.title} event succesfully",
                    messageText=messageText,
                )
            except Exception as err:
                logger.error(
                    "PublishEvent: Failed to send confrimation email due to exception"
                )
                logger.exception(err)
            return render(
                request, "tools/new-event/published.html", dataclasses.asdict(result)
            )
        elif (
            result.type
            == EventAutomationDriver.Result.ResultType.UNRESOLVEABLE_CONFLICT
        ):
            # Let the user know about the unresolveable conflict
            logger.info(
                "PublishEvent: Event Publish Failed with Unresolveable Conflict %s",
                str(result),
            )
            # Convert conflict times to timezone specified in form, then make naiive
            timezone = pytz.timezone(form.cleaned_data[NewEventForm.Keys.TIMEZONE])
            for i in range(len(result.conflicts)):
                result.conflicts[i].start = result.conflicts[i].start.astimezone(
                    timezone
                )
                result.conflicts[i].start = result.conflicts[i].start.replace(
                    tzinfo=None
                )
                result.conflicts[i].end = result.conflicts[i].end.astimezone(timezone)
                result.conflicts[i].end = result.conflicts[i].end.replace(tzinfo=None)
            return render(
                request,
                "tools/new-event/unresolveable.html",
                dataclasses.asdict(result),
            )
        elif result.type == EventAutomationDriver.Result.ResultType.CONFLICT:
            # Let the user know about the conflict and ask if they want to ignore it
            logger.info(
                "PublishEvent: Event Publish Failed with Unresolveable Conflict %s",
                str(result),
            )
            # Convert conflict times to timezone specified in form, , then make naiive
            timezone = pytz.timezone(form.cleaned_data[NewEventForm.Keys.TIMEZONE])
            for i in range(len(result.conflicts)):
                result.conflicts[i].start = result.conflicts[i].start.astimezone(
                    timezone
                )
                result.conflicts[i].start = result.conflicts[i].start.replace(
                    tzinfo=None
                )
                result.conflicts[i].end = result.conflicts[i].end.astimezone(timezone)
                result.conflicts[i].end = result.conflicts[i].end.replace(tzinfo=None)
            return render(
                request, "tools/new-event/resolveable.html", dataclasses.asdict(result)
            )
        else:
            # Some unkown error occured, show the user all informaiton we have
            logger.error(
                "PublishEvent: Unexpected error when publishing event %s", str(result)
            )
            return render(
                request, "tools/new-event/unknown.html", dataclasses.asdict(result)
            )

        return HttpResponseRedirect("/")
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
            return render(request, "tools/new-delegated-event/unknown.html", {"errorStr": "The form could not be validated, please go back and try again."})

        eventInfo = form.convertToEventInfo()
        logger.info("PublishDelegatedEvent: Recieved event data %s", str(eventInfo))

        logger.info("PublishDelegatedEvent: Getting Owner")
        formOwner = form.cleaned_data[NewEventForm.Keys.OWNER]
        try:
            owner = EventOwners.objects.get(name = formOwner)
        except Exception as err:
            logger.exception("PublishDelegatedEvent: Could not get event owner")
            raise err
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
            utcStart = eventInfo.start.astimezone(pytz.utc)
            utcEnd = eventInfo.end.astimezone(pytz.utc)
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
            timezone = pytz.timezone(form.cleaned_data[NewEventForm.Keys.TIMEZONE])
            for i in range(len(result.conflicts)):
                result.conflicts[i].start = result.conflicts[i].start.astimezone(
                    timezone
                )
                result.conflicts[i].start = result.conflicts[i].start.replace(
                    tzinfo=None
                )
                result.conflicts[i].end = result.conflicts[i].end.astimezone(timezone)
                result.conflicts[i].end = result.conflicts[i].end.replace(tzinfo=None)
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
            # Convert conflict times to timezone specified in form, , then make naiive
            timezone = pytz.timezone(form.cleaned_data[NewEventForm.Keys.TIMEZONE])
            for i in range(len(result.conflicts)):
                result.conflicts[i].start = result.conflicts[i].start.astimezone(
                    timezone
                )
                result.conflicts[i].start = result.conflicts[i].start.replace(
                    tzinfo=None
                )
                result.conflicts[i].end = result.conflicts[i].end.astimezone(timezone)
                result.conflicts[i].end = result.conflicts[i].end.replace(tzinfo=None)
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
            logger.info("ApprovedDelegateEvent: Getting configuration")
            zoomConfig = SecretManager.getZoomConfig()
            anConfig = SecretManager.getANAutomatorConfig()
            gCalConfig = SecretManager.getGCalConfig()
            ignoreResolveableConflicts = True
            logger.info("ApprovedDelegateEvent: Attempting to publish event")
            result = EventAutomationDriver.publishEvent(
                eventInfo=eventInfo,
                config=EventAutomationDriver.Config(
                    zoomConfig=zoomConfig,
                    anConfig=anConfig,
                    gCalConfig=gCalConfig,
                    ignoreResolveableConflicts=ignoreResolveableConflicts
                )
            )
            # Left around for debugging, useful if you want to test thigns out without having to actuall publish a bunch of stuff
            # result = EventAutomationDriver.Result(EventAutomationDriver.Result.ResultType.PUBLISHED, anManageLink="manageLink", anShareLink="shareLink", gCalLink="gCalLink", zoomAccount="Account", zoomLink="zoomLink")
            if result.type == EventAutomationDriver.Result.ResultType.PUBLISHED:
                # Return success and links back to user
                logger.info(
                    "ApprovedDelegateEvent: Event Publish sucessfully with result %s", str(result)
                )
                logger.info(
                    "ApprovedDelegateEvent: Sending confirmation email to user %s and %s",
                    str(request.user.email),
                    str(event.creator.email)
                )
                # Save event to database
                utcNow = datetime.datetime.now(datetime.UTC)
                event.status = DelegatedEvents.Status.APPROVED
                event.approver = request.user
                event.dateReviewed = utcNow
                event.reason = formData[ApproveDelegatedEventForm.Keys.REASON]
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
                                                authorizer = request.user,
                                                owner = event.owner,
                                                reason = formData[ApproveDelegatedEventForm.Keys.REASON])
                e.save()

                # Send email
                try:
                    # TODO: potentially replace with Django built in mail module
                    messageText = f""" Your event {eventInfo.title} was approved by {request.user.getUserNameString()} published successfully. Here are the links.
                    Zoom Link ({result.zoomAccount}): {result.zoomLink}
                    AN Share Link: {result.anShareLink}
                    AN Manage Link: {result.anManageLink}
                    Google Calendar Link: {result.gCalLink}"""
                    EmailApi.sendEmailFromWebsiteAccount(
                        toAddress=request.user.email,
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
                        "ApprovedDelegateEvent: Failed to send confrimation email due to exception"
                    )
                    logger.exception(err)
                return redirect("delegated-event-detail", pk=id)
            elif (
                result.type
                == EventAutomationDriver.Result.ResultType.UNRESOLVEABLE_CONFLICT
            ):
                # Let the user know about the unresolveable conflict
                logger.info(
                    "ApprovedDelegateEvent: Event Publish Failed with Unresolveable Conflict %s",
                    str(result),
                )
                # Convert conflict times to timezone specified in form, then make naiive
                timezone = pytz.timezone(event.timezone)
                for i in range(len(result.conflicts)):
                    result.conflicts[i].start = result.conflicts[i].start.astimezone(
                        timezone
                    )
                    result.conflicts[i].start = result.conflicts[i].start.replace(
                        tzinfo=None
                    )
                    result.conflicts[i].end = result.conflicts[i].end.astimezone(timezone)
                    result.conflicts[i].end = result.conflicts[i].end.replace(tzinfo=None)
                return render(
                    request,
                    "tools/approve-delegated-event/unresolveable.html",
                    dataclasses.asdict(result),
                )
            else:
                # Some unkown error occured, show the user all informaiton we have
                logger.error(
                    "ApprovedDelegateEvent: Unexpected error when publishing event %s", str(result)
                )
                return render(
                    request, "tools/approve-delegated-event/unknown.html", dataclasses.asdict(result)
                )

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