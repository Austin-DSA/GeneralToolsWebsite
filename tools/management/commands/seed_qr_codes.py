"""Seed the standing Austin DSA QR codes.

Idempotently mints the QR codes the chapter prints on flyers / table tents.
Each code targets a *link tree item* (not a raw URL) so scans are attributed to
that item in analytics and the code stays repointable - edit the item's URL in
admin and the printed code follows, no reprint.

Currently seeds:

  * "become-a-member" → the public tree's "Join Austin DSA!" item
    (act.dsausa.org membership signup), scan URL /qr/become-a-member/.

Idempotent: re-running updates the existing code in place (matched on its
slug). Run after `seed_link_trees` so the target item exists.

    python manage.py seed_qr_codes
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from tools.models import LinkTree, LinkTreeItem, QRCode

# Each entry: (code, label, campaign, treeSlug, itemLabel)
QR_CODES = [
    (
        "become-a-member",
        "Become a member (Join Austin DSA!)",
        "flyer",
        "links",
        "Join Austin DSA!",
    ),
]


class Command(BaseCommand):
    help = "Create/refresh the standing QR codes (e.g. the 'become a member' code) targeting their link tree items."

    @transaction.atomic
    def handle(self, *args, **options):
        for code, label, campaign, treeSlug, itemLabel in QR_CODES:
            try:
                tree = LinkTree.objects.get(slug=treeSlug)
            except LinkTree.DoesNotExist:
                raise SystemExit(
                    f"Tree '{treeSlug}' not found - run `python manage.py seed_link_trees` first."
                )
            try:
                item = tree.items.get(label=itemLabel, kind=LinkTreeItem.Kind.MANUAL)
            except LinkTreeItem.DoesNotExist:
                raise SystemExit(
                    f"Item '{itemLabel}' not found in tree '{treeSlug}' - re-run `seed_link_trees`."
                )

            qr, created = QRCode.objects.update_or_create(
                code=code,
                defaults={
                    "label": label,
                    "campaign": campaign,
                    "tree": None,
                    "item": item,
                    "rawUrl": "",
                    "isActive": True,
                },
            )
            # full_clean enforces the exactly-one-target invariant (clean()).
            qr.full_clean()

            verb = "Created" if created else "Updated"
            destination, _, _ = qr.resolveTarget()
            self.stdout.write(self.style.SUCCESS(
                f"{verb} QR '{qr.code}' -> {item.label} ({destination}); scan URL {qr.scanUrl()}"
            ))
