import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse

import settings

from . import permissions
from .forms import AccessRequestForm, ReviewAccessRequestForm
from .models import AccessRequests, User

logger = logging.getLogger(__name__)

# NOTE: unlike the event flows this uses Django's built-in mail module (the
# TODOs in eventViews suggest moving there anyway): it honors the configured
# EMAIL_BACKEND (console in dev — handy for grabbing the review link) and is
# assertable in tests. Send failures are logged and never fail the request —
# same convention as everywhere else in this app.


def _getApproversFor(accessRequest: AccessRequests):
    """Everyone who may act on this request: superusers, holders of
    approveAccessRequest (directly or via a group), and — for group requests —
    existing members of the requested group. The requester is excluded."""
    approvePermission = Permission.objects.filter(
        codename=permissions.APPROVE_ACCESS_REQUEST.split(".")[1],
        content_type__app_label="tools",
    ).first()
    query = Q(is_superuser=True)
    if approvePermission is not None:
        query |= Q(user_permissions=approvePermission) | Q(groups__permissions=approvePermission)
    else:
        # Shouldn't happen once migrations have run; superusers still get notified
        logger.error("AccessRequests: approveAccessRequest permission row is missing")
    if accessRequest.group is not None:
        query |= Q(groups=accessRequest.group)
    return (
        User.objects.filter(query, is_active=True)
        .exclude(id=accessRequest.requester_id)
        .distinct()
    )


def _sendNewRequestEmails(request, accessRequest: AccessRequests):
    try:
        reviewLink = request.build_absolute_uri(accessRequest.getUrl())
        messageText = f"""{accessRequest.getRequesterName()} has requested the following access to Austin DSA Tools:
        {accessRequest.getTargetDescription()}

        Their reason: {accessRequest.justification}

        Please visit {reviewLink} to approve or deny the request."""
        for approver in _getApproversFor(accessRequest):
            send_mail(
                subject=f"Access requested: {accessRequest.getTargetDescription()}",
                message=messageText,
                from_email=settings.EMAIL_HOST_USER,
                recipient_list=[approver.email],
            )
        send_mail(
            subject="Your access request was submitted",
            message=f"""Your request for {accessRequest.getTargetDescription()} has been sent to the approvers. You will receive an email once it has been reviewed.""",
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=[accessRequest.requester.email],
        )
    except Exception as err:
        logger.error("RequestAccess: Failed to send request notification emails")
        logger.exception(err)


def _sendDecisionEmail(accessRequest: AccessRequests):
    if accessRequest.requester is None:
        return
    try:
        decision = "approved" if accessRequest.status == AccessRequests.Status.APPROVED else "denied"
        send_mail(
            subject=f"Your access request was {decision}",
            message=f"""Your request for {accessRequest.getTargetDescription()} was {decision} by {accessRequest.getReviewerName()}.
            Reason: {accessRequest.reason}""",
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=[accessRequest.requester.email],
        )
    except Exception as err:
        logger.error("ReviewAccessRequest: Failed to send decision email")
        logger.exception(err)


@login_required
def request_access(request):
    """Any logged-in member may ask for group membership or one of the custom
    tools.* permissions — no permission required, since fresh self-registered
    accounts start with none."""
    if request.method == "POST":
        form = AccessRequestForm(request.user, request.POST)
        if not form.is_valid():
            return render(request, "tools/access-requests/request.html", {"form": form})

        accessRequest = AccessRequests.objects.create(
            requester=request.user,
            group=form.cleaned_data["group"],
            permission=form.cleaned_data["permission"],
            justification=form.cleaned_data[AccessRequestForm.Keys.JUSTIFICATION],
            status=AccessRequests.Status.REQUESTED,
        )
        logger.info(
            "RequestAccess: %s requested %s",
            request.user.getUserNameString(),
            accessRequest.getTargetDescription(),
        )
        _sendNewRequestEmails(request, accessRequest)
        return render(
            request, "tools/access-requests/created.html", {"accessRequest": accessRequest}
        )

    return render(
        request, "tools/access-requests/request.html", {"form": AccessRequestForm(request.user)}
    )


@login_required
def access_request_list(request):
    myRequests = AccessRequests.objects.filter(requester=request.user).order_by("-dateCreated")
    pending = (
        AccessRequests.objects.filter(status=AccessRequests.Status.REQUESTED)
        .exclude(requester=request.user)
        .order_by("-dateCreated")
    )
    actionable = [r for r in pending if r.canBeReviewedBy(request.user)]
    return render(
        request,
        "tools/access-requests/list.html",
        {"myRequests": myRequests, "actionable": actionable},
    )


@login_required
def review_access_request(request, id):
    try:
        accessRequest = AccessRequests.objects.get(id=id)
    except AccessRequests.DoesNotExist:
        # Render the same page as the not-authorized case so probing ids
        # doesn't reveal which requests exist
        logger.info("ReviewAccessRequest: Request %s does not exist", id)
        return render(request, "tools/access-requests/unauthorized.html")

    # Only the requester and would-be reviewers may see the request at all
    isRequester = accessRequest.requester_id == request.user.id
    if not isRequester and not accessRequest.canBeReviewedBy(request.user):
        logger.error(
            "ReviewAccessRequest: User %s is not allowed to review request %s",
            request.user.email,
            str(accessRequest.id),
        )
        return render(request, "tools/access-requests/unauthorized.html")

    canAct = (
        accessRequest.status == AccessRequests.Status.REQUESTED
        and accessRequest.canBeReviewedBy(request.user)
    )

    if request.method == "POST" and canAct:
        form = ReviewAccessRequestForm(request.POST)
        if not form.is_valid():
            logger.error("ReviewAccessRequest: Submitted form is not valid")
            return render(
                request,
                "tools/access-requests/review.html",
                {"accessRequest": accessRequest, "form": form, "canAct": True},
            )
        formData = form.cleaned_data
        # Serialize concurrent reviewers: re-read the row under a lock and
        # re-check the status so two simultaneous approvals can't both win
        # (and the audit fields reflect whoever actually decided first).
        with transaction.atomic():
            accessRequest = AccessRequests.objects.select_for_update().get(id=id)
            if accessRequest.status != AccessRequests.Status.REQUESTED:
                logger.info(
                    "ReviewAccessRequest: Request %d was already reviewed, ignoring decision from %s",
                    accessRequest.id,
                    request.user.getUserNameString(),
                )
                return render(
                    request,
                    "tools/access-requests/review.html",
                    {"accessRequest": accessRequest, "canAct": False},
                )
            if formData[ReviewAccessRequestForm.Keys.APPROVE] == "YES":
                logger.info(
                    "ReviewAccessRequest: %s approved request %d",
                    request.user.getUserNameString(),
                    accessRequest.id,
                )
                if accessRequest.requester is not None:
                    accessRequest.grantTo(accessRequest.requester)
                accessRequest.status = AccessRequests.Status.APPROVED
            else:
                logger.info(
                    "ReviewAccessRequest: %s denied request %d",
                    request.user.getUserNameString(),
                    accessRequest.id,
                )
                accessRequest.status = AccessRequests.Status.DENIED
            accessRequest.reviewer = request.user
            accessRequest.dateReviewed = datetime.datetime.now(datetime.UTC)
            accessRequest.reason = formData[ReviewAccessRequestForm.Keys.REASON]
            accessRequest.save()
        # The grant is already committed — a failed email never rolls it back
        _sendDecisionEmail(accessRequest)
        return render(
            request,
            "tools/access-requests/review.html",
            {"accessRequest": accessRequest, "canAct": False},
        )

    context = {"accessRequest": accessRequest, "canAct": canAct}
    if canAct:
        context["form"] = ReviewAccessRequestForm()
    return render(request, "tools/access-requests/review.html", context)
