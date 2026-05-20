import logging
import json
import datetime

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from models import Resolution, ResolutionVote
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView

from utils import sessionDataRequired
import permissions


logger = logging.getLogger(__name__)

# TODO: Add user voting later, most everyone will be using the guest mode at first

## Vote Admin Views

class ResolutionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = permissions.VIEW_RESOLUTIONS
    model = Resolution
    template_name = "tools/voting/resolutions/list.html"

class ResolutionDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView)
    permission_required = permissions.VIEW_RESOLUTIONS
    model = Resolution
    template_name = "tools/voting/resolutions/details.html" # TODO


@login_required
@permission_required(permissions.VALIDATE_VOTES)
def validateResolution(request, resId):
    # TODO:
    return HttpResponse()

@login_required
@permission_required(permissions.CREATE_RESOLUTION)
def createNewResolution(request):
    # TODO:
    return HttpResponse()

@login_required
@permission_required(permissions.CREATE_RESOLUTION)
def editResolution(request, resId):
    # TODO:
    return HttpResponse()

## Guest Voting

@sessionDataRequired(sessionKeys=["email", "name"], redirectURL="guest-login")
def guestBallotView(request):
    # Gather all resolutions that are currently open
    n = datetime.datetime.now()
    resolutionQuery = Resolution.objects.filter(votingOpen__lt=n).filter(votingClose__gt=n)
    resolutionsSerialized = []
    # Expected context
    # {resolutions : [{id: uniqueIDStr, title: str, author: str, textUrl: str, voteStatus: str}]}
    for r in resolutionQuery:
        resolutionsSerialized.append({
            "id" : str(r.pk),
            "title" : r.name,
            "author" : r.author,
            "textUrl" : r.textUrl,
            # Always return None here, that way someone can't see someone elses vote if the use the guest login with a different email
            "voteStatus" : "None"
        })
    return render(request, "tools/voting/ballot.html", {"resolutions" : resolutionsSerialized})

@sessionDataRequired(sessionKeys=["email", "name"], redirectURL="guest-login")
def guestProcessVote(request, resId):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    resolution = get_object_or_404(Resolution, resId)
    email = request.session["email"]
    name = request.session["name"]
    # If the resolution is no longer open then just do nothing
    if not resolution.isOpen():
        return HttpResponseBadRequest("Resolution Closed for voting")
    vote = None
    try:
        bodyObj = json.loads(request.body)
        if "vote" not in bodyObj:
            raise Exception()
        rawVote = bodyObj["vote"]
        vote = ResolutionVote.getChoiceForString(rawVote)
        if vote is None:
            raise Exception()
    except Exception as _:
        return HttpResponseBadRequest("Invalid Body")
    # Just create a new vote record
    # If someone wants to changee their vote it will be recorded twice
    # Would rather spend extra time computing which votes need to be redone
    # Compared to checking if someone has already voted to update their row
    # Voting will likely happen in bursts at GBM so keeping that low latency is paramount
    voteObj = ResolutionVote(
        vote = vote,
        email = email,
        name = name
    )
    voteObj.save()
    return HttpResponse("Success")
