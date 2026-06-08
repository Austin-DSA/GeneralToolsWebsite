"""Seed the 'Link Tree Maintainers' auth group.

A non-superuser maintainer needs TWO different kinds of permission to do the
whole link-tree job, because the surfaces are gated differently (see below):

  * the custom ``tools.manageLinkTree`` permission -> gates the QR image view
    (/qr/<code>/image) and is the documented "maintainer" gate;
  * the standard Django per-model add/change/delete/view permissions on
    LinkTree / LinkTreeItem / QRCode -> gate the admin CRUD that actually
    creates and edits those rows (the admin classes use no custom gate).

This command bundles both into one group so a single grant is enough. It also
adds ``tools.viewLinkMetrics`` so a maintainer who mints QR codes can see the
scans they generate.

IMPORTANT: a Group only confers *permissions*. Admin access additionally
requires the per-user ``is_staff`` flag, which a group cannot set -- set it on
each member in /admin/ (or via createsuperuser for full access).

Idempotent: re-running re-syncs the group's permission set exactly.

    python manage.py seed_link_tree_groups
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from tools import permissions as perms
from tools.permissions import PermissionRights
from tools.models import LinkTree, LinkTreeItem, QRCode

GROUP_NAME = "Link Tree Maintainers"

# Custom permissions (codename only -- they live on the unmanaged
# PermissionRights model's content type).
CUSTOM_CODENAMES = [perms._MANAGE_LINK_TREE, perms._VIEW_LINK_METRICS]

# Standard model CRUD permissions, one set per managed model.
CRUD_ACTIONS = ("add", "change", "delete", "view")
CRUD_MODELS = (LinkTree, LinkTreeItem, QRCode)


class Command(BaseCommand):
    help = "Create/refresh the 'Link Tree Maintainers' group with the full link-tree permission set."

    @transaction.atomic
    def handle(self, *args, **options):
        wanted = []
        missing = []

        # Custom permissions hang off the (unmanaged) PermissionRights model.
        customCt = ContentType.objects.get_for_model(PermissionRights)
        for codename in CUSTOM_CODENAMES:
            perm = Permission.objects.filter(content_type=customCt, codename=codename).first()
            (wanted if perm else missing).append(perm or f"tools.{codename}")

        # Default add/change/delete/view permissions per model.
        for model in CRUD_MODELS:
            ct = ContentType.objects.get_for_model(model)
            for action in CRUD_ACTIONS:
                codename = f"{action}_{model._meta.model_name}"
                perm = Permission.objects.filter(content_type=ct, codename=codename).first()
                (wanted if perm else missing).append(perm or f"tools.{codename}")

        if missing:
            # Permissions are created by `migrate` (post_migrate). If any are
            # absent, the DB is mid-migration -- fail loudly rather than seed a
            # half-empty group.
            raise SystemExit(
                "Missing permissions (run `python manage.py migrate` first): "
                + ", ".join(missing)
            )

        group, created = Group.objects.get_or_create(name=GROUP_NAME)
        group.permissions.set(wanted)  # set() makes re-runs exactly re-sync.

        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} group '{GROUP_NAME}' with {len(wanted)} permissions."
        ))
        for perm in sorted(wanted, key=lambda p: p.codename):
            self.stdout.write(f"  - tools.{perm.codename}")
        self.stdout.write(self.style.WARNING(
            "Reminder: add users to this group in /admin/ AND set is_staff=True "
            "on each -- a group cannot grant admin access by itself."
        ))
