"""Front-end management of EventOwners (the entities delegated events hang
off): list with health badges, create, edit with typeahead authorizer
assignment, and an unstick action for requests stranded against owners that
can no longer approve anything.

Mirrors the Manage Groups views (accessViews.py) - same function-based shape,
same delta-based membership editing, same typeahead search endpoint.
"""
import datetime
import logging

import pytz

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect

from . import permissions
from . import utils
from .EmailApi import EmailApi
from .forms import CHAPTER_TIMEZONE, EventOwnerForm
from .models import DelegatedEvents, EventOwners, User

logger = logging.getLogger(__name__)


def _expirationCentralStr(owner: EventOwners) -> str:
    """The stored UTC expiration rendered in chapter (Central) time, matching
    the getStartLocalizedStr convention on the event models."""
    expiration = owner.expiration
    if expiration.tzinfo is None or expiration.tzinfo.utcoffset(expiration) is None:
        expiration = pytz.utc.localize(expiration)
    return expiration.astimezone(pytz.timezone(CHAPTER_TIMEZONE)).strftime(utils.DATE_TIME_FORMAT)


def _ownerFormInitial(owner: EventOwners) -> dict:
    """Initial values for editing: the stored UTC expiration rendered back as
    naive Central Time for the datetime-local input (localize-at-the-edges)."""
    expiration = owner.expiration
    if expiration.tzinfo is None or expiration.tzinfo.utcoffset(expiration) is None:
        expiration = pytz.utc.localize(expiration)
    localExpiration = expiration.astimezone(pytz.timezone(CHAPTER_TIMEZONE)).replace(tzinfo=None)
    return {
        EventOwnerForm.Keys.NAME: owner.name,
        EventOwnerForm.Keys.EXPIRATION: localExpiration,
        EventOwnerForm.Keys.IS_PERMANENT: owner.isPermanent,
    }


def _whyOwnerIsStuck(ownerIsActive: bool, ownerAuthorizerCount: int) -> str:
    """Display priority: expiration trumps the empty roster."""
    if not ownerIsActive:
        return "Owner expired"
    if ownerAuthorizerCount == 0:
        return "No authorizers"
    return ""


@login_required
@permission_required(permissions.MANAGE_EVENT_OWNERS)
def manage_event_owners(request):
    """List every owner with its health (active / expired / no authorizers),
    plus an actionable card of requests stuck against unhealthy owners."""
    # One query for all pending requests; rows whose owner was deleted in
    # /admin/ have owner=None (SET_NULL) and can't be unstuck here - skip them.
    pendingRequestsByOwnerId = {}
    pendingRequests = (
        DelegatedEvents.objects
        .filter(status=DelegatedEvents.Status.REQUESTED, owner__isnull=False)
        .select_related("creator")
        .order_by("dateCreated")
    )
    for event in pendingRequests:
        pendingRequestsByOwnerId.setdefault(event.owner_id, []).append(event)

    ownerRows = []
    stuckRequestRows = []
    for owner in EventOwners.objects.prefetch_related("authorizers").order_by("name"):
        ownerIsActive = owner.isActive()
        ownerAuthorizerCount = len(owner.authorizers.all())  # uses the prefetch cache
        ownerIsHealthy = ownerIsActive and ownerAuthorizerCount > 0
        ownerStuckRequests = [] if ownerIsHealthy else pendingRequestsByOwnerId.get(owner.id, [])
        ownerRows.append({
            "owner": owner,
            "ownerIsActive": ownerIsActive,
            "ownerAuthorizerCount": ownerAuthorizerCount,
            "ownerIsHealthy": ownerIsHealthy,
            "ownerStuckRequestCount": len(ownerStuckRequests),
            "ownerExpirationStr": _expirationCentralStr(owner),
        })
        whyStuck = _whyOwnerIsStuck(ownerIsActive, ownerAuthorizerCount)
        for event in ownerStuckRequests:
            stuckRequestRows.append({
                "event": event,
                "owner": owner,
                "whyStuck": whyStuck,
            })

    return render(request, "tools/event-owners/list.html", {
        "ownerRows": ownerRows,
        "stuckRequestRows": stuckRequestRows,
    })


@login_required
@permission_required(permissions.MANAGE_EVENT_OWNERS)
def manage_event_owner(request, ownerId):
    """Edit one owner: name, expiration/permanence, and the authorizer roster
    (submitted as deltas, see EventOwnerForm). Also lists this owner's stuck
    requests with a cancel action when the owner can't approve anything."""
    try:
        owner = EventOwners.objects.prefetch_related("authorizers").get(id=ownerId)
    except EventOwners.DoesNotExist:
        return redirect("manage-event-owners")

    ownerEditSaved = False
    if request.method == "POST":
        form = EventOwnerForm(request.POST, owner=owner)
        if form.is_valid():
            currentAuthorizerIds = set(owner.authorizers.values_list("id", flat=True))
            addedAuthorizers = [
                authorizer for authorizer in form.cleaned_data[EventOwnerForm.Keys.ADD_AUTHORIZERS]
                if authorizer.id not in currentAuthorizerIds
            ]
            removedAuthorizers = list(form.cleaned_data[EventOwnerForm.Keys.REMOVE_AUTHORIZERS])
            resultingAuthorizerIds = (
                currentAuthorizerIds | {authorizer.id for authorizer in addedAuthorizers}
            ) - {authorizer.id for authorizer in removedAuthorizers}
            pendingRequestCount = DelegatedEvents.objects.filter(
                owner=owner, status=DelegatedEvents.Status.REQUESTED
            ).count()
            if not resultingAuthorizerIds and pendingRequestCount:
                form.add_error(
                    None,
                    "Cannot remove the last authorizer while event requests are "
                    "pending - cancel the pending requests below first.",
                )
            else:
                owner.name = form.cleaned_data[EventOwnerForm.Keys.NAME]
                owner.expiration = form.cleaned_data[EventOwnerForm.Keys.EXPIRATION]
                owner.isPermanent = form.cleaned_data[EventOwnerForm.Keys.IS_PERMANENT]
                owner.save()
                owner.authorizers.add(*addedAuthorizers)
                owner.authorizers.remove(*removedAuthorizers)
                logger.info(
                    "ManageEventOwners: %s saved owner '%s' isPermanent=%s expiration=%s added=%s removed=%s",
                    request.user.getUserNameString(),
                    owner.name,
                    owner.isPermanent,
                    owner.expiration,
                    [authorizer.username for authorizer in addedAuthorizers],
                    [authorizer.username for authorizer in removedAuthorizers],
                )
                ownerEditSaved = True
                # Re-fetch so the roster below reflects the just-saved deltas
                owner = EventOwners.objects.prefetch_related("authorizers").get(id=ownerId)
    else:
        form = EventOwnerForm(owner=owner, initial=_ownerFormInitial(owner))

    # Computed in Python and passed as plain booleans - templates must never
    # touch owner.isActive directly (it's a method; the bare attribute is
    # always truthy, the exact bug new_event had).
    ownerIsActive = owner.isActive()
    ownerAuthorizerRows = [
        {"user": authorizer}
        for authorizer in owner.authorizers.filter(is_active=True).order_by("username")
    ]
    ownerAuthorizerCount = len(ownerAuthorizerRows)
    ownerIsHealthy = ownerIsActive and ownerAuthorizerCount > 0
    ownerStuckRequests = []
    if not ownerIsHealthy:
        ownerStuckRequests = list(
            DelegatedEvents.objects
            .filter(owner=owner, status=DelegatedEvents.Status.REQUESTED)
            .select_related("creator")
            .order_by("dateCreated")
        )

    return render(request, "tools/event-owners/detail.html", {
        "owner": owner,
        "form": form,
        "ownerEditSaved": ownerEditSaved,
        "ownerIsActive": ownerIsActive,
        "ownerAuthorizerRows": ownerAuthorizerRows,
        "ownerAuthorizerCount": ownerAuthorizerCount,
        "ownerIsHealthy": ownerIsHealthy,
        "ownerStuckRequests": ownerStuckRequests,
    })


@login_required
@permission_required(permissions.MANAGE_EVENT_OWNERS)
def create_event_owner(request):
    """Dedicated create page (name + expiration policy), then straight into
    the edit page to add authorizers - an owner without authorizers is the
    exact broken state this feature exists to surface."""
    if request.method == "POST":
        form = EventOwnerForm(request.POST)
        if form.is_valid():
            owner = EventOwners.objects.create(
                name=form.cleaned_data[EventOwnerForm.Keys.NAME],
                expiration=form.cleaned_data[EventOwnerForm.Keys.EXPIRATION],
                isPermanent=form.cleaned_data[EventOwnerForm.Keys.IS_PERMANENT],
            )
            logger.info(
                "ManageEventOwners: %s created owner '%s'",
                request.user.getUserNameString(), owner.name,
            )
            return redirect("manage-event-owner", ownerId=owner.id)
    else:
        form = EventOwnerForm()
    return render(request, "tools/event-owners/create.html", {"form": form})


@login_required
@permission_required(permissions.MANAGE_EVENT_OWNERS)
def manage_event_owner_authorizer_search(request, ownerId):
    """Typeahead backing for the owner page's add-authorizer box: the top
    matches among active users who aren't already authorizers."""
    try:
        owner = EventOwners.objects.get(id=ownerId)
    except EventOwners.DoesNotExist:
        return JsonResponse({"results": []})

    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    matches = (
        User.objects.filter(is_active=True)
        .filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )
        .exclude(eventAuthorizations=owner)
        .order_by("username")[:10]
    )
    return JsonResponse({"results": [{
        "id": authorizer.id,
        "username": authorizer.username,
        "fullName": f"{authorizer.first_name} {authorizer.last_name}".strip(),
        "email": authorizer.email,
    } for authorizer in matches]})


@login_required
@permission_required(permissions.MANAGE_EVENT_OWNERS)
def cancel_stuck_delegated_event(request, ownerId, eventId):
    """Administrative unstick: deny a REQUESTED row whose owner can never
    approve it (expired, or nobody in the roster). Deliberately refuses to
    touch a healthy owner's pending requests - that decision belongs to the
    owner's authorizers via the normal approve flow."""
    if request.method != "POST":
        return redirect("manage-event-owner", ownerId=ownerId)
    try:
        event = DelegatedEvents.objects.select_related("owner", "creator").get(id=eventId)
    except DelegatedEvents.DoesNotExist:
        return redirect("manage-event-owners")

    # URL consistency: the stuckness check below runs against event.owner, so
    # a crafted POST pairing a healthy owner's event with a stuck owner's id
    # must be rejected, not evaluated against the wrong owner. A null owner
    # (deleted in /admin/) is treated the same way.
    if event.owner is None or event.owner.id != ownerId:
        logger.warning(
            "CancelStuckDelegatedEvent: %s sent a cancel for event %d with mismatched owner id %d",
            request.user.getUserNameString(), event.id, ownerId,
        )
        return redirect("manage-event-owners")
    owner = event.owner

    if event.status != DelegatedEvents.Status.REQUESTED:
        return redirect("manage-event-owner", ownerId=owner.id)

    ownerIsStuck = owner.authorizers.count() == 0 or not owner.isActive()
    if not ownerIsStuck:
        logger.warning(
            "CancelStuckDelegatedEvent: %s tried to cancel event %d but owner '%s' is healthy",
            request.user.getUserNameString(), event.id, owner.name,
        )
        return redirect("manage-event-owner", ownerId=owner.id)

    event.status = DelegatedEvents.Status.DENIED
    event.approver = request.user
    event.dateReviewed = datetime.datetime.now(datetime.UTC)
    event.reason = (
        "Cancelled by an event-owner manager - the owner could not approve "
        "this request (expired or no authorizers)."
    )
    event.save()
    logger.info(
        "CancelStuckDelegatedEvent: %s cancelled stuck event %d for owner '%s'",
        request.user.getUserNameString(), event.id, owner.name,
    )

    # The cancellation is already committed - a failed email never rolls it back
    try:
        if event.creator is not None:
            messageText = (
                f"Your event request for {event.title} was cancelled - the event owner "
                f"{owner.name} could not approve it (expired or has no authorizers). "
                "Please contact your chapter to arrange a new owner."
            )
            EmailApi.sendEmailFromWebsiteAccount(
                toAddress=event.creator.email,
                subject=f"{event.title} was cancelled",
                messageText=messageText,
            )
    except Exception as err:
        logger.error("CancelStuckDelegatedEvent: Failed to send cancellation email")
        logger.exception(err)

    return redirect("manage-event-owner", ownerId=owner.id)
