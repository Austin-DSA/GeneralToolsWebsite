from django.db import models

def _publicPermissionName(perm: str) -> str:
    return "tools."+perm

_PUBLISH_EVENT = "publishEvent"
PUBLISH_EVENT = _publicPermissionName(_PUBLISH_EVENT)
_REQUEST_DELEGATED_EVENT = "requestDelegatedEvent"
REQUEST_DELEGATED_EVENT = _publicPermissionName(_REQUEST_DELEGATED_EVENT)
_APPROVE_DELEGATED_EVENT = "approveDelegatedEvent"
APPROVE_DELEGATED_EVENT = _publicPermissionName(_APPROVE_DELEGATED_EVENT)

class PermissionRights(models.Model):
    class Meta:
        managed = False

        default_permissions = ()

        permissions = (
            (_PUBLISH_EVENT, 'Allowed to publish events'),
            (_REQUEST_DELEGATED_EVENT, 'Allowed to request delegated events'),
            (_APPROVE_DELEGATED_EVENT, 'Allowed to approve delegated events')
        )