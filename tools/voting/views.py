import logging
import json
import datetime
import uuid

from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed, HttpResponseNotFound
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.views import View
from .models import Resolution, ResolutionVote
from django.views.generic.detail import DetailView
from django.views.generic.list import ListView
from .forms import NewResolutionForm, EditResolutionForm

from .tasks import validateVotes, emailVoteResolutionResult, emailFailedVotesForResolution

from utils import sessionDataRequired
import permissions


logger = logging.getLogger(__name__)

# TODO: Add user voting later when we have a good auth setup, most everyone will be using the guest mode at first

## Resolution Views

#GET /resolutions
class ResolutionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = permissions.VIEW_RESOLUTIONS
    model = Resolution
    template_name = "tools/voting/resolutions/list.html"

#GET /resolution/<id>/
class ResolutionDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    permission_required = permissions.VIEW_RESOLUTIONS
    model = Resolution
    template_name = "tools/voting/resolutions/details.html"

# In-memory Map from resolutionIds to validations
# We could store these more persistently but we really only want this to determine if validation is finished
# Active validations during a reboot will be lost but that is fine
activeValidationTasks = {}

# /resolution/<id>/validate
class VoteValidationEndpoint(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = permissions.VALIDATE_VOTES
    # Starts a validation for a given resolution id
    # If an active validation exists and isn't finished will return true
    # If an active validation exists and is finished, will start a new one
    # 200 - Started or validation in progress
    # 404 - Resolution not found
    def post(request, resId):
        doesResolutionExist = Resolution.objects.filter(id=resId).exists()
        if not doesResolutionExist:
            return HttpResponseNotFound("Resolution not found")
        if resId in activeValidationTasks:
            task = activeValidationTasks[resId]
            if not task.is_ready():
                return HttpResponse("In Progress", status = 200)
            # If it is finished then start a new one
        result = validateVotes(resId)
        activeValidationTasks[resId] = result
        return HttpResponse("Started", status=200)

    # Returns a status code depending on task status
    # 200- Task Complete
    # 201- Task in Progress
    # 400 - Task Failed
    # 404 - Task not Found
    def get(request, resId):
        if taskId not in activeValidationTasks:
            return HttpResponseNotFound("Task not found")
        task = activeValidationTasks[taskId]
        # Task has completed, delete from map and then tell the user we are done
        if task.is_ready():
            activeValidationTasks[taskId] = None
            return HttpResponse("Finished", status=200)
        return HttpResponse("In Progress", status=201)


# /resolution/<id>/emailResults
class EmailResolutionResults(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = permissions.VALIDATE_VOTES

    # 200 - Started
    # 404 - DNE
    def post(request, resId):
        doesResolutionExist = Resolution.objects.filter(id=resId).exists()
        if doesResolutionExist:
            emailVoteResolutionResult.schedule(args=(resId), delay=1)
            return HttpResponse("Sending", status=200)
        else:
            return HttpResponseNotFound("Resolution does not exist")

# /resolution/<id>/emailFailedValidations
class EmailInvalidVotes(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = permissions.VALIDATE_VOTES

    def post(request, resId):
        doesResolutionExist = Resolution.objects.filter(id=resId).exists()
        if doesResolutionExist:
            emailFailedVotesForResolution.schedule(args=(resId), delay=1)
            return HttpResponse("Sending", status=200)
        else:
            return HttpResponseNotFound("Resolution does not exist")

# /resolution/<id>/results
class ResolutionResults(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = permissions.VALIDATE_VOTES

    def get(request, resId):
        # JS will handle the loading screen and another 
        resolution : Resolution = get_object_or_404(Resolution, resId)
        votes = ResolutionVote.objects.filter(resolution=resolution)
        errorVotes = []
        for vote in votes:
            if vote.verificationError != ResolutionVote.VerificationError.NoError:
                errorVotes.append(vote)
        return render(request, 'tools/voting/resolutions/validate.html', {"res" : resolution, "errorVotes" : errorVotes})


# /resolution/new
class NewResolution(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = permissions.CREATE_RESOLUTION
    def get(request):
        form = NewResolutionForm()
        return render(request, "tools/voting/resolutions/new.html", { "form" : form })
    
    def post(request):
        form = NewResolutionForm(request.POST)
        data = form.getData()
        if data is None:
            logger.error("NewResolution: Submitted form is not valid")
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

# /resolution/<id>/edit
class EditResolution(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = permissions.CREATE_RESOLUTION

    def get(request, resId):
        resolution : Resolution = get_object_or_404(Resolution, resId)
        form = EditResolutionForm.getFormFromResolution(resolution)
        return render(request, "tools/voting/resolutions/new.html", { "form" : form })

    def post(request, resId):
        resolution : Resolution = get_object_or_404(Resolution, resId)
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


## Guest Voting

# /voting/guestBallot
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

# POST /voting/submit/<id>
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
