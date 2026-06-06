"""Builders for test objects. No assertions, no client, no settings — just rows."""
from django.contrib.auth.models import Group, Permission

from tools.models import User

DEFAULT_PASSWORD = "s3cure-pw-123"


def permission(codename):
    """A custom tools.* permission row (registered on PermissionRights)."""
    return Permission.objects.get(codename=codename, content_type__app_label="tools")


class UserFactory:
    """Stateless user builder. Mirrors the old _makeUser contract exactly so
    existing call sites translate 1:1 (UserFactory.make('x') == _makeUser('x'))."""

    @staticmethod
    def make(username, email=None, password=DEFAULT_PASSWORD, perms=(), groups=(), **kwargs):
        user = User.objects.create_user(
            username=username,
            email=email or f"{username}@example.com",
            password=password,
            **kwargs,
        )
        for codename in perms:                 # convenience: grant custom perms inline
            user.user_permissions.add(permission(codename))
        for group in groups:
            user.groups.add(group)
        return user

    @staticmethod
    def admin(username="admin", **kwargs):
        """Holder of approveAccessRequest (the access-feature 'admin')."""
        return UserFactory.make(username, perms=("approveAccessRequest",), **kwargs)

    @staticmethod
    def superuser(username="admin", **kwargs):
        return UserFactory.make(username, is_superuser=True, **kwargs)


def refetchForPerms(user):
    """Return a fresh User so Django's per-instance permission cache is clean —
    makes the 'grant then re-read to test has_perm' idiom explicit (was an
    inline `User.objects.get(id=...)` at tests.py:663 and :926)."""
    return User.objects.get(id=user.id)
