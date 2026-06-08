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

_CREATE_RESOLUTION = "createResolution"
CREATE_RESOLUTION = _publicPermissionName(_CREATE_RESOLUTION)
_VIEW_RESOLUTIONS = "viewResolutions"
VIEW_RESOLUTIONS = _publicPermissionName(_VIEW_RESOLUTIONS)
_VALIDATE_VOTES = "validateVotes"
VALIDATE_VOTES = _publicPermissionName(_VALIDATE_VOTES)

_MANAGE_LINK_TREE = "manageLinkTree"
MANAGE_LINK_TREE = _publicPermissionName(_MANAGE_LINK_TREE)
_VIEW_LINK_METRICS = "viewLinkMetrics"
VIEW_LINK_METRICS = _publicPermissionName(_VIEW_LINK_METRICS)

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
            (_CREATE_RESOLUTION, 'Allowed to create new resolutions'),
            (_VIEW_RESOLUTIONS, 'Allowed to view the resolutions'),
            (_VALIDATE_VOTES, 'Allowed to run vote validation'),
            (_MANAGE_LINK_TREE, 'Allowed to manage link trees, items, and QR codes'),
            (_VIEW_LINK_METRICS, 'Allowed to view link tree click/scan metrics'),
        )
