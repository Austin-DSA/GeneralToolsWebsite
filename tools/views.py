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

from .EventAutomation import EventAutomationDriver
from .SecretManager import SecretManager
from .forms import NewEventForm, NewDelegatedEventForm
from .EmailApi import EmailApi
import permissions
import dataclasses
import models

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class PageOption:
    href : str
    title : str
    permission : str

    def getOptionDict(self):
        return {"href" : self.href, "title": self.title}
    
PAGES = [
    PageOption(href="new-event", title="Create an Event", permission=permissions.PUBLISH_EVENT),
    PageOption(href="new-delegated-event", title="Create Delegated Event Request", permission=permissions.REQUEST_DELEGATED_EVENT)
]



def getPagesForUser(user) -> list[dict[str,str]]:
    pagesForUser = [x.getOptionDict() for x in PAGES if user.has_perm(x.permission)]
    return pagesForUser


@login_required
def index(request):
    options = getPagesForUser(request.user)
    return render(request, "tools/home.html", {options : options})

@login_required
@permission_required(permissions.PUBLISH_EVENT)
def new_event(request):
    # TODO: Should maybe split displaying form from processing to different views?
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
            owner = models.EventOwners.objects.get(name = formOwner)
        except Exception as err:
            logger.exception("PublishEvent: Could not get event owner")
            raise err
        
        if not owner.isActive:
            logger.error("PublishEvent: Owner %s is no longer active and cannot create events", owner.name)
            return render(request, "tools/new-event/unknown.html", {"errorStr": f"Owner {owner.name} is no longer active and cannot create"})
        if request.user not in owner.authorizers:
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
            utcEnd = eventInfo.start.astimezone(pytz.utc)
            utcNow = datetime.datetime.now(datetime.UTC)
            e = models.PostedEvents.objects.create(title = eventInfo.title)
            e.start = utcStart
            e.end = utcEnd
            e.locationName = eventInfo.locationName
            e.streetAddress = eventInfo.streetAddress
            e.city = eventInfo.city
            e.state = eventInfo.state
            e.zip = eventInfo.zip
            e.country = eventInfo.country
            e.description = eventInfo.description
            e.instructions = eventInfo.instructions
            e.dateCreated = utcNow
            e.datePublished = utcNow
            if result.anManageLink is not None:
                e.anManageLink = result.anManageLink
            if result.anShareLink is not None:
                e.anShareLink = result.anShareLink
            if result.gCalLink is not None:
                e.gCalLink = result.gCalLink
            if result.zoomLink is not None:
                e.zoomLink = result.zoomLink
            if result.zoomAccount is not None:
                e.zoomAccount = result.zoomAccount
            e.creator = request.user
            e.authorizer = request.user
            e.owner = owner
            e.reasonApproved = "Created by approved authorizer"
            e.save()

            # Send email
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

@login_required
@permission_required(permissions.REQUEST_DELEGATED_EVENT)
def new_delegated_event(request):
    if request.method == "POST":
        logger.info("PublishDelegatedEvent: Recieved submission of event to publish.")
        form = NewDelegatedEventForm(request.POST)
        if not form.is_valid():
            logger.error("PublishDelegatedEvent: Submitted Form is not valid")
            return render(request, "tools/new-delegated-event/unknown.html", {"errorStr": "The form could not be validated, please go back and try again."})

        eventInfo = form.convertToEventInfo()
        logger.info("PublishDelegatedEvent: Recieved event data %s", str(eventInfo))

        logger.info("PublishDelegatedEvent: Getting Owner")
        formOwner = form.cleaned_data[NewEventForm.Keys.OWNER]
        try:
            owner = models.EventOwners.objects.get(name = formOwner)
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
            utcEnd = eventInfo.start.astimezone(pytz.utc)
            utcNow = datetime.datetime.now(datetime.UTC)
            e = models.DelegatedEvents.objects.create(title = eventInfo.title)
            e.start = utcStart
            e.end = utcEnd
            e.locationName = eventInfo.locationName
            e.streetAddress = eventInfo.streetAddress
            e.city = eventInfo.city
            e.state = eventInfo.state
            e.zip = eventInfo.zip
            e.country = eventInfo.country
            e.description = eventInfo.description
            e.instructions = eventInfo.instructions
            e.dateCreated = utcNow
            e.creator = request.user
            e.owner = owner
            e.save()
            # Email authorizers that a new event has been requested
            try:
                # TODO: potentially replace with Django built in mail module
                # TODO: Add in approve/reject view
                messageText = f"""
                A new event request has been created for {owner.name}, of which you are an authorized approver.
                Please visit {reverse("tools:approve-delegated-event", kwargs={ "id" :e.id})} to either approve or reject the event.
                """
                for approver in owner.authorizers:
                    EmailApi.sendEmailFromWebsiteAccount(
                        toAddress=approver.email,
                        subject=f"Event {eventInfo.title} has been requested",
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
        form = NewDelegatedEventForm(
            initial={
                "startTime": datetime.datetime.now(),
                "endTime": datetime.datetime.now(),
            }
        )
        return render(request, "tools/new-delegated-event/new.html", {"form": form})
    
@login_required
@permission_required(permissions.APPROVE_DELEGATED_EVENT)
def approve_delegated_event(request, id):
    # Check if the delegated event exists
    event = None
    try:
        event = models.DelegatedEvents.objects.get(id=id)
    except Exception as err:
        logger.exception("ApproveDelegatedEvent: Could not retrieve delegated event request %s due to unexpected error %s", id, err)
        return render(request, "tools/approve-delegated-event/error.html", {"errorStr" : str(err)})
    
    # Check if the event had already been approved/denied, if so redirect to the page that views a delegated event
    if event.state != models.DelegatedEvents.State.REQUESTED:
        logger.info("ApproveDelegatedEvent: Event %s is already approved or denied, redirecting to detail view", str(id))
        return redirect("tools:delegated-events", id=id)
    
    # Check if the current user is allowed to approve
    if request.user not in event.owner.authorizers:
        logger.error("ApproveDelegatedEvent: User %s does not have authorization to approve event %s for owner %s", request.user.email, str(event.id), event.owner.name)
        return render(request, "tools/approve-delegated-event/unauthorized.html", {"owner" : event.owner.name})

    if request.method == "POST":
        # Process approve/disapprove
        pass
    else:
        # Check for conflicts and then send out approval form
        pass