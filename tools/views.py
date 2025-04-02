from django.shortcuts import render
from django.http import HttpResponseRedirect
from .forms import NewEventForm
import datetime
import time
import logging
import dataclasses
import pytz

from .EventAutomation import EventAutomationDriver
from .SecretManager import SecretManager

def index(request):
    return render(request, "tools/home.html", {})


def new_event(request):
    # TODO: Should maybe split displaying form from processing to different views?
    # TODO: Email confirmation
    if request.method == "POST":
        logging.info("PublishEvent: Recieved submission of event to publish.")
        form = NewEventForm(request.POST)
        if not form.is_valid():
            logging.error("PublishEvent: Submitted Form is not valid")
            return HttpResponseRedirect("/")
        
        eventInfo = form.convertToEventInfo()
        logging.info("PublishEvent: Recieved event data %s", str(eventInfo))
        
        logging.info("PublishEvent: Getting configuration")
        zoomConfig = SecretManager.getZoomConfig()
        anConfig = SecretManager.getANAutomatorConfig()
        gCalConfig = SecretManager.getGCalConfig()

        ignoreResolveableConflicts = form.cleaned_data[NewEventForm.Keys.IGNORE_RESOLVEABLE_CONFLICTS]

        logging.info("PublishEvent: Attempting to publish event")
        result = EventAutomationDriver.publishEvent(eventInfo=eventInfo, 
                                                    config=EventAutomationDriver.Config(zoomConfig=zoomConfig, anConfig=anConfig, gCalConfig=gCalConfig, ignoreResolveableConflicts=ignoreResolveableConflicts))
        
        if result.type == EventAutomationDriver.Result.ResultType.PUBLISHED:
            # Return success and links back to user
            # TODO: Send confirmation email to user email - need to get user emails set up, can re-use email api from membership upload list
            logging.info("PublishEvent: Event Publish sucessfully with result %s", str(result))
            return render(request, "tools/new-event/published.html", dataclasses.asdict(result))
        elif result.type == EventAutomationDriver.Result.ResultType.UNRESOLVEABLE_CONFLICT:
            # Let the user know about the unresolveable conflict
            logging.info("PublishEvent: Event Publish Failed with Unresolveable Conflict %s", str(result))
            # Convert conflict times to timezone specified in form, then make naiive
            timezone = pytz.timezone(form.cleaned_data[NewEventForm.Keys.TIMEZONE])
            for i in range(len(result.conflicts)):
                result.conflicts[i].start = result.conflicts[i].start.astimezone(timezone)
                result.conflicts[i].start = result.conflicts[i].start.replace(tzinfo=None)
                result.conflicts[i].end = result.conflicts[i].end.astimezone(timezone)
                result.conflicts[i].end = result.conflicts[i].end.replace(tzinfo=None)
            return render(request, "tools/new-event/unresolveable.html", dataclasses.asdict(result))
        elif result.type == EventAutomationDriver.Result.ResultType.CONFLICT:
            # Let the user know about the conflict and ask if they want to ignore it
            logging.info("PublishEvent: Event Publish Failed with Unresolveable Conflict %s", str(result))
            # Convert conflict times to timezone specified in form, , then make naiive
            timezone = pytz.timezone(form.cleaned_data[NewEventForm.Keys.TIMEZONE])
            for i in range(len(result.conflicts)):
                result.conflicts[i].start = result.conflicts[i].start.astimezone(timezone)
                result.conflicts[i].start = result.conflicts[i].start.replace(tzinfo=None)
                result.conflicts[i].end = result.conflicts[i].end.astimezone(timezone)
                result.conflicts[i].end = result.conflicts[i].end.replace(tzinfo=None)
            return render(request, "tools/new-event/resolveable.html", dataclasses.asdict(result))
        else:
            # Some unkown error occured, show the user all informaiton we have 
            logging.error("PublishEvent: Unexpected error when publishing event %s", str(result))
            return render(request, "tools/new-event/unknown.html", dataclasses.asdict(result))

        return HttpResponseRedirect("/")
    else:
        form = NewEventForm(
            initial={
                "startTime": datetime.datetime.now(),
                "endTime": datetime.datetime.now(),
            }
        )
        return render(request, "tools/new-event/new.html", {"form": form})
