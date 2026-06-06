from django.db import models

def _publicPermissionName(perm: str) -> str:
    return "tools."+perm

_PUBLISH_EVENT = "publishEvent"
PUBLISH_EVENT = _publicPermissionName(_PUBLISH_EVENT)
_VIEW_PUBLISHED_EVENTS = "viewPublishedEventList"
VIEW_PUBLISHED_EVENTS = _publicPermissionName(_VIEW_PUBLISHED_EVENTS)

_REQUEST_DELEGATED_EVENT = "requestDelegatedEvent"
REQUEST_DELEGATED_EVENT = _publicPermissionName(_REQUEST_DELEGATED_EVENT)
_APPROVE_DELEGATED_EVENT = "approveDelegatedEvent"
APPROVE_DELEGATED_EVENT = _publicPermissionName(_APPROVE_DELEGATED_EVENT)
_VIEW_DELEGATED_EVENTS = "viewDelegatedEventList"
VIEW_DELEGATED_EVENTS = _publicPermissionName(_VIEW_DELEGATED_EVENTS)

_MANAGE_LINK_TREE = "manageLinkTree"
MANAGE_LINK_TREE = _publicPermissionName(_MANAGE_LINK_TREE)
_VIEW_LINK_METRICS = "viewLinkMetrics"
VIEW_LINK_METRICS = _publicPermissionName(_VIEW_LINK_METRICS)

_APPROVE_ACCESS_REQUEST = "approveAccessRequest"
APPROVE_ACCESS_REQUEST = _publicPermissionName(_APPROVE_ACCESS_REQUEST)


def getRequestablePermissions():
    """The permissions a member may ask for on the request-access page.

    Scoped to the custom permissions registered on PermissionRights below —
    users request things like manageLinkTree, never Django's internal model
    CRUD permissions (add_user, delete_linktree, ...).
    """
    # Imported here: this module is imported by models.py at startup, before
    # the app registry is ready for auth model imports.
    from django.contrib.auth.models import Permission

    return Permission.objects.filter(
        content_type__app_label="tools",
        content_type__model="permissionrights",
    ).order_by("name")

class PermissionRights(models.Model):
    class Meta:
        managed = False

        default_permissions = ()

        permissions = (
            (_PUBLISH_EVENT, 'Allowed to publish events'),
            (_REQUEST_DELEGATED_EVENT, 'Allowed to request delegated events'),
            (_APPROVE_DELEGATED_EVENT, 'Allowed to approve delegated events'),
            (_VIEW_DELEGATED_EVENTS, 'Allowed to view delegated events'),
            (_VIEW_PUBLISHED_EVENTS, 'Allowed to view published events'),
            (_MANAGE_LINK_TREE, 'Allowed to manage link trees, items, and QR codes'),
            (_VIEW_LINK_METRICS, 'Allowed to view link tree click/scan metrics'),
            (_APPROVE_ACCESS_REQUEST, 'Allowed to approve or deny any access request'),
        )