import datetime
import logging
import urllib.parse

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import Group, Permission
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse

import settings

from . import permissions
from .forms import AccessRequestForm, GroupForm, ManageAccessForm, ReviewAccessRequestForm
from .models import AccessRequests, User

logger = logging.getLogger(__name__)

# NOTE: unlike the event flows this uses Django's built-in mail module (the
# TODOs in eventViews suggest moving there anyway): it honors the configured
# EMAIL_BACKEND (console in dev - handy for grabbing the review link) and is
# assertable in tests. Send failures are logged and never fail the request -
# same convention as everywhere else in this app.


def _getApproversFor(accessRequest: AccessRequests):
    """Everyone who may act on this request: superusers, holders of
    approveAccessRequest (directly or via a group), and - for group requests -
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
        messageText = f"""{accessRequest.getRequesterName()} has requested the following access on Party Line (Austin DSA):
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
    tools.* permissions - no permission required, since fresh self-registered
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
def my_access(request):
    """What the logged-in member can currently do, and where each piece of
    access comes from."""
    groupsInfo = []
    for group in request.user.groups.prefetch_related("permissions__content_type").order_by("name"):
        groupsInfo.append({
            "name": group.name,
            "permissionNames": [
                permission.name
                for permission in group.permissions.all()
                if permission.content_type.model == "permissionrights"
            ],
        })

    directIds = set(request.user.user_permissions.values_list("id", flat=True))
    heldPermissions = []
    for permission in permissions.getRequestablePermissions():
        if not request.user.has_perm("tools." + permission.codename):
            continue
        if request.user.is_superuser:
            source = "Superuser"
        elif permission.id in directIds:
            source = "Granted directly"
        else:
            viaGroups = request.user.groups.filter(permissions=permission)
            source = "Via " + ", ".join(group.name for group in viaGroups) if viaGroups else "Granted directly"
        heldPermissions.append({"name": permission.name, "source": source})

    return render(request, "tools/access/my-access.html", {
        "groupsInfo": groupsInfo,
        "heldPermissions": heldPermissions,
    })


@login_required
@permission_required(permissions.APPROVE_ACCESS_REQUEST)
def manage_access(request):
    users = (
        User.objects.filter(is_active=True)
        .prefetch_related("groups")
        .order_by("username")
    )
    customIds = set(permissions.getRequestablePermissions().values_list("id", flat=True))
    rows = []
    for member in users:
        rows.append({
            "user": member,
            "groups": list(member.groups.all()),
            "directPermissionCount": member.user_permissions.filter(id__in=customIds).count(),
        })
    return render(request, "tools/access/manage-list.html", {"rows": rows})


@login_required
@permission_required(permissions.APPROVE_ACCESS_REQUEST)
def manage_access_user(request, userId):
    try:
        target = User.objects.get(id=userId)
    except User.DoesNotExist:
        return redirect("manage-access")

    customIds = set(permissions.getRequestablePermissions().values_list("id", flat=True))
    saved = False
    if request.method == "POST":
        form = ManageAccessForm(request.POST)
        if form.is_valid():
            target.groups.set(form.cleaned_data[ManageAccessForm.Keys.GROUPS])
            # Only manage the custom tools.* permissions here - leave any other
            # directly-assigned permissions (e.g. model perms for staff) alone
            keepOthers = list(target.user_permissions.exclude(id__in=customIds))
            target.user_permissions.set(
                keepOthers + list(form.cleaned_data[ManageAccessForm.Keys.PERMISSIONS])
            )
            logger.info(
                "ManageAccess: %s set %s groups=%s directPerms=%s",
                request.user.getUserNameString(),
                target.getUserNameString(),
                [group.name for group in form.cleaned_data[ManageAccessForm.Keys.GROUPS]],
                [permission.codename for permission in form.cleaned_data[ManageAccessForm.Keys.PERMISSIONS]],
            )
            # A direct grant satisfies any pending request for the same thing -
            # close those out so they stop cluttering approver queues
            selectedGroupIds = {group.id for group in form.cleaned_data[ManageAccessForm.Keys.GROUPS]}
            selectedPermissionIds = {
                permission.id for permission in form.cleaned_data[ManageAccessForm.Keys.PERMISSIONS]
            }
            pendingRequests = AccessRequests.objects.filter(
                requester=target, status=AccessRequests.Status.REQUESTED
            )
            for pending in pendingRequests:
                satisfied = (
                    (pending.group_id is not None and pending.group_id in selectedGroupIds)
                    or (pending.permission_id is not None and pending.permission_id in selectedPermissionIds)
                )
                if not satisfied:
                    continue
                logger.info(
                    "ManageAccess: closing pending request %d - access granted directly",
                    pending.id,
                )
                pending.status = AccessRequests.Status.APPROVED
                pending.reviewer = request.user
                pending.dateReviewed = datetime.datetime.now(datetime.UTC)
                pending.reason = "Access granted directly"
                pending.save()
                _sendDecisionEmail(pending)
            saved = True
    else:
        form = ManageAccessForm(initial={
            ManageAccessForm.Keys.GROUPS: target.groups.all(),
            ManageAccessForm.Keys.PERMISSIONS: target.user_permissions.filter(id__in=customIds),
        })

    # Hand the template self-describing rows (rendered as plain checkboxes
    # named groups/permissions, which is exactly what ManageAccessForm parses
    # back on POST). Built after the save so a successful POST shows the
    # member's new state.
    groups = list(Group.objects.prefetch_related("permissions").order_by("name"))
    targetGroupIds = set(target.groups.values_list("id", flat=True))
    targetDirectIds = set(
        target.user_permissions.filter(id__in=customIds).values_list("id", flat=True)
    )

    groupRows = []
    for group in groups:
        customPermissions = [p for p in group.permissions.all() if p.id in customIds]
        groupRows.append({
            "group": group,
            "permissionNames": [permissions.shortPermissionLabel(p.name) for p in customPermissions],
            "checked": group.id in targetGroupIds,
        })

    allPermissions = list(permissions.getRequestablePermissions())
    grantedBy = {permission.id: [] for permission in allPermissions}
    for group in groups:
        for groupPermission in group.permissions.all():
            if groupPermission.id in grantedBy:
                grantedBy[groupPermission.id].append(group.id)

    byCategory = {}
    for permission in allPermissions:
        category = permissions.getPermissionCategory(permission.codename)
        byCategory.setdefault(category, []).append(permission)

    categoryOrder = [title for title, _ in permissions.PERMISSION_CATEGORIES] + ["Other"]
    permissionSections = []
    for title in categoryOrder:
        if title not in byCategory:
            continue
        permissionSections.append({
            "title": title,
            "rows": [{
                "permission": permission,
                "shortLabel": permissions.shortPermissionLabel(permission.name),
                "viaGroupIds": ",".join(str(groupId) for groupId in grantedBy[permission.id]),
                "checked": permission.id in targetDirectIds,
            } for permission in byCategory[title]],
        })

    return render(request, "tools/access/manage-user.html", {
        "target": target,
        "form": form,
        "saved": saved,
        "groupRows": groupRows,
        "permissionSections": permissionSections,
    })


@login_required
@permission_required(permissions.APPROVE_ACCESS_REQUEST)
def manage_groups(request):
    createForm = GroupForm()
    if request.method == "POST":
        createForm = GroupForm(request.POST)
        if createForm.is_valid():
            group = Group.objects.create(name=createForm.cleaned_data[GroupForm.Keys.NAME])
            logger.info(
                "ManageGroups: %s created group '%s'",
                request.user.getUserNameString(), group.name,
            )
            return redirect("manage-group", groupId=group.id)

    customIds = set(permissions.getRequestablePermissions().values_list("id", flat=True))
    rows = []
    for group in Group.objects.prefetch_related("permissions").order_by("name"):
        customPermissions = [p for p in group.permissions.all() if p.id in customIds]
        rows.append({
            "group": group,
            "memberCount": group.user_set.filter(is_active=True).count(),
            "permissionNames": [permissions.shortPermissionLabel(p.name) for p in customPermissions],
        })
    return render(request, "tools/access/manage-groups.html", {
        "rows": rows,
        "createForm": createForm,
        "deletedName": request.GET.get("deleted", ""),
    })


@login_required
@permission_required(permissions.APPROVE_ACCESS_REQUEST)
def manage_group(request, groupId):
    try:
        group = Group.objects.get(id=groupId)
    except Group.DoesNotExist:
        return redirect("manage-groups")

    customIds = set(permissions.getRequestablePermissions().values_list("id", flat=True))
    saved = False
    if request.method == "POST":
        form = GroupForm(request.POST, group=group)
        if form.is_valid():
            previousMemberIds = set(group.user_set.values_list("id", flat=True))
            group.name = form.cleaned_data[GroupForm.Keys.NAME]
            group.save()
            # Only manage the custom tools.* permissions here - leave any model
            # permissions attached in /admin/ alone (mirrors manage_access_user)
            keepOtherPermissions = list(group.permissions.exclude(id__in=customIds))
            group.permissions.set(
                keepOtherPermissions + list(form.cleaned_data[GroupForm.Keys.PERMISSIONS])
            )
            # Membership changes arrive as deltas (see GroupForm), so members
            # not named in the request are never touched
            addedMembers = [
                member for member in form.cleaned_data[GroupForm.Keys.ADD_MEMBERS]
                if member.id not in previousMemberIds
            ]
            group.user_set.add(*addedMembers)
            group.user_set.remove(*form.cleaned_data[GroupForm.Keys.REMOVE_MEMBERS])
            logger.info(
                "ManageGroups: %s set group '%s' permissions=%s added=%s removed=%s",
                request.user.getUserNameString(),
                group.name,
                [p.codename for p in form.cleaned_data[GroupForm.Keys.PERMISSIONS]],
                [member.username for member in addedMembers],
                [member.username for member in form.cleaned_data[GroupForm.Keys.REMOVE_MEMBERS]],
            )
            # Adding someone here satisfies their pending request for this group -
            # close those out, same as a direct grant on the member page
            newMemberIds = {member.id for member in addedMembers}
            if newMemberIds:
                pendingRequests = AccessRequests.objects.filter(
                    group=group,
                    status=AccessRequests.Status.REQUESTED,
                    requester_id__in=newMemberIds,
                )
                for pending in pendingRequests:
                    logger.info(
                        "ManageGroups: closing pending request %d - access granted directly",
                        pending.id,
                    )
                    pending.status = AccessRequests.Status.APPROVED
                    pending.reviewer = request.user
                    pending.dateReviewed = datetime.datetime.now(datetime.UTC)
                    pending.reason = "Access granted directly"
                    pending.save()
                    _sendDecisionEmail(pending)
            saved = True
    else:
        form = GroupForm(group=group, initial={GroupForm.Keys.NAME: group.name})

    # Same self-describing-row pattern as manage_access_user; built after the
    # save so a successful POST shows the group's new state
    groupPermissionIds = set(group.permissions.values_list("id", flat=True))
    allPermissions = list(permissions.getRequestablePermissions())
    byCategory = {}
    for permission in allPermissions:
        category = permissions.getPermissionCategory(permission.codename)
        byCategory.setdefault(category, []).append(permission)

    categoryOrder = [title for title, _ in permissions.PERMISSION_CATEGORIES] + ["Other"]
    permissionSections = []
    for title in categoryOrder:
        if title not in byCategory:
            continue
        permissionSections.append({
            "title": title,
            "rows": [{
                "permission": permission,
                "shortLabel": permissions.shortPermissionLabel(permission.name),
                "checked": permission.id in groupPermissionIds,
            } for permission in byCategory[title]],
        })

    # Only the current roster renders; additions come through the search
    # endpoint below, so the page stays light no matter how big the org gets
    memberRows = [
        {"user": member}
        for member in group.user_set.filter(is_active=True).order_by("username")
    ]
    activeMemberCount = len(memberRows)

    return render(request, "tools/access/manage-group.html", {
        "group": group,
        "form": form,
        "saved": saved,
        "permissionSections": permissionSections,
        "memberRows": memberRows,
        "activeMemberCount": activeMemberCount,
    })


@login_required
@permission_required(permissions.APPROVE_ACCESS_REQUEST)
def manage_group_member_search(request, groupId):
    """Typeahead backing for the group page's add-member box: the top matches
    among active users who aren't already in the group."""
    try:
        group = Group.objects.get(id=groupId)
    except Group.DoesNotExist:
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
        .exclude(groups=group)
        .order_by("username")[:10]
    )
    return JsonResponse({"results": [{
        "id": member.id,
        "username": member.username,
        "fullName": f"{member.first_name} {member.last_name}".strip(),
        "email": member.email,
    } for member in matches]})


@login_required
@permission_required(permissions.APPROVE_ACCESS_REQUEST)
def manage_group_delete(request, groupId):
    if request.method != "POST":
        return redirect("manage-groups")
    try:
        group = Group.objects.get(id=groupId)
    except Group.DoesNotExist:
        return redirect("manage-groups")

    # The page's JS keeps the delete button disabled until the typed name
    # matches; this is the server-side backstop.
    if request.POST.get("confirmName", "").strip() != group.name:
        logger.warning(
            "ManageGroups: %s sent a delete for '%s' with a mismatched confirmation",
            request.user.getUserNameString(), group.name,
        )
        return redirect("manage-group", groupId=group.id)

    # A pending request for a deleted group can never be granted - deny it
    # with an explanation rather than leaving it stranded in approver queues.
    # (AccessRequests.group is SET_NULL, so reviewed history survives.)
    pendingRequests = AccessRequests.objects.filter(
        group=group, status=AccessRequests.Status.REQUESTED
    )
    for pending in pendingRequests:
        pending.status = AccessRequests.Status.DENIED
        pending.reviewer = request.user
        pending.dateReviewed = datetime.datetime.now(datetime.UTC)
        pending.reason = f"The group '{group.name}' was deleted"
        pending.save()
        _sendDecisionEmail(pending)

    deletedName = group.name
    memberCount = group.user_set.count()
    group.delete()
    logger.info(
        "ManageGroups: %s deleted group '%s' (%d members at deletion)",
        request.user.getUserNameString(), deletedName, memberCount,
    )
    return redirect(
        reverse("manage-groups") + "?" + urllib.parse.urlencode({"deleted": deletedName})
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
        # The grant is already committed - a failed email never rolls it back
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
