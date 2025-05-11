import datetime
import time
import logging
import dataclasses
import pytz

from django.shortcuts import render
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import permission_required, login_required
from django.contrib.auth.models import User

from .EventAutomation import EventAutomationDriver
from .SecretManager import SecretManager
from .forms import NewEventForm
from .EmailApi import EmailApi

logger = logging.getLogger(__name__)


@login_required
def index(request):
    return render(request, "tools/home.html", {})


@permission_required("tools.publishEvent")
def new_event(request):
    # TODO: Should maybe split displaying form from processing to different views?
    if request.method == "POST":
        logger.info("PublishEvent: Recieved submission of event to publish.")
        form = NewEventForm(request.POST)
        if not form.is_valid():
            logger.error("PublishEvent: Submitted Form is not valid")
            return HttpResponseRedirect("/")

        eventInfo = form.convertToEventInfo()
        logger.info("PublishEvent: Recieved event data %s", str(eventInfo))

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
                ignoreResolveableConflicts=ignoreResolveableConflicts,
            ),
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
