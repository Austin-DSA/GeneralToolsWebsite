import logging
import json
import datetime

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from .models import Resolution, ResolutionVote
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from .forms import NewResolutionForm, EditResolutionForm

from utils import sessionDataRequired
import permissions0


logger = logging.getLogger(__name__)

# TODO: Add user voting later when we have a good auth setup, most everyone will be using the guest mode at first

## Vote Admin Views

class ResolutionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = permissions.VIEW_RESOLUTIONS
    model = Resolution
    template_name = "tools/voting/resolutions/list.html"

class ResolutionDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = permissions.VIEW_RESOLUTIONS
    model = Resolution
    template_name = "tools/voting/resolutions/details.html"

@login_required
@permission_required(permissions.VALIDATE_VOTES)
def startVoteValidation(request, resId):
    return HttpResponse()

@login_required
@permission_required(permissions.VALIDATE_VOTES)
def getValidationStatus(request, taskId):
    return HttpResponse()

@login_required
@permission_required(permissions.VALIDATE_VOTES)
def emailResolutionResults(request, resId):
    return HttpResponse()

@login_required
@permission_required(permissions.VALIDATE_VOTES)
def emailFailedVotesResults(request, resId):
    return HttpResponse()

@login_required
@permission_required(permissions.VALIDATE_VOTES)
def resolutionValidationInfo(request, resId):
    # JS will handle the loading screen and another 
    resolution : Resolution = get_object_or_404(Resolution, resId)
    votes = ResolutionVote.objects.filter(resolution=resolution)
    errorVotes = []
    for vote in votes:
        if vote.verificationError != ResolutionVote.VerificationError.NoError:
            errorVotes.append(vote)
    return render(request, 'tools/voting/resolutions/validate.html', {"res" : resolution, "errorVotes" : errorVotes})

@login_required
@permission_required(permissions.CREATE_RESOLUTION)
def createNewResolution(request):
    if request.method == "POST":
        form = EditResolutionForm(request.POST)
        data = form.getData()
        if data is None:
            logger.error("EditResolution: Submitted form is not valid")
            return render(request, "tools/common/error.html", {"errorStr": "The form could not be validated, please go back and try again."})
        resolution = Resolution.objects.create(
            name=data.name,
            textUrl=data.textUrl,
            author=data.author,
            votingOpen=data.voteOpenUTC,
            votingClose=data.voteCloseUTC,
            timezone=data.timezone
        )
        resolution.save()
        return redirect("resolution-detail", pk=resolution.pk)
    else:
        form = NewResolutionForm()
        return render(request, "tools/voting/resolutions/new.html", { "form" : form })

@login_required
@permission_required(permissions.CREATE_RESOLUTION)
def editResolution(request, resId):
    resolution : Resolution = get_object_or_404(Resolution, resId)
    if request.method == "POST":
        form = EditResolutionForm(request.POST)
        data = form.getData()
        if data is None:
            logger.error("EditResolution: Submitted form is not valid")
            return render(request, "tools/common/error.html", {"errorStr": "The form could not be validated, please go back and try again."})
        resolution.name = data.name
        resolution.author = data.author
        resolution.textUrl = data.textUrl
        resolution.timezone = data.timezone
        resolution.votingOpen = data.voteOpenUTC
        resolution.votingClose = data.voteCloseUTC
        resolution.save()
        return redirect("resolution-detail", pk=resId)
    else:
        form = EditResolutionForm.getFormFromResolution(resolution)
        return render(request, "tools/voting/resolutions/new.html", { "form" : form })

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
    # Voting will likely happen in bursts at GBM so keeping that low latency is important
    voteObj = ResolutionVote(
        vote = vote,
        email = email,
        name = name
    )
    voteObj.save()
    return HttpResponse("Success")
