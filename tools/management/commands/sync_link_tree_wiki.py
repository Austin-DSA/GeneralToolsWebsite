"""Resolve Link Tree wiki items against the Outline wiki.

Link tree items of kind WIKI surface live wiki content (e.g. "the latest GBM
agenda"). This command resolves each one to a concrete document URL + title and
caches the result on the item, so the public tree page never has to call Outline
at request time (and keeps working if Outline is briefly down).

Runs under the dedicated read-only Outline service-account token
(SecretManager.getOutlineReadConfig), which needs the `documents.search` scope.

Schedule via host cron / Windows Task Scheduler — there is no in-process
scheduler. A daily run is plenty; agendas don't change minute to minute.

Run from the repo root:
    python manage.py sync_link_tree_wiki [--dry-run] [--quiet]
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from tools.LinkTree import WikiLinkResolver
from tools.models import LinkTreeItem
from tools.SecretManager import SecretManager
from tools.WikiAutomation.OutlineAPI import OutlineAPI

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Resolve link tree wiki items to live Outline documents and cache the result."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Resolve and report only; do not write the cache back to items.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-item lines (the summary still prints).",
        )

    def handle(self, *args, **options):
        dryRun = options["dry_run"]
        quiet = options["quiet"]

        config = SecretManager.getOutlineReadConfig()
        if config is None:
            # Outline read token not configured — wiki surfacing is opt-in.
            # Benign: WIKI items just stay unresolved/hidden. Exit 0.
            self.stdout.write(self.style.WARNING(
                "Outline read token not configured (OutlineBaseUrl / "
                "OutlineReadApiToken) — skipping wiki resolution. WIKI link "
                "items will remain unresolved and hidden."
            ))
            return

        try:
            api = OutlineAPI(config)
        except Exception as e:
            # Misconfigured secrets — nothing can be resolved. Surface it.
            self.stderr.write(self.style.ERROR(f"FATAL: could not build Outline client: {e}"))
            raise SystemExit(1)

        if dryRun:
            self.stdout.write(self.style.WARNING("DRY RUN — items will not be updated."))

        items = list(LinkTreeItem.objects.filter(kind=LinkTreeItem.Kind.WIKI))
        resolved = 0
        unresolved = 0
        errored = 0

        for item in items:
            try:
                result = self._resolve(api, item)
            except Exception:
                errored += 1
                logger.exception("Error resolving wiki item %s", item.pk)
                if not quiet:
                    self.stderr.write(self.style.ERROR(f"  [ERROR] item {item.pk} ({item.tree_id})"))
                continue

            if result is None:
                unresolved += 1
                if not quiet:
                    self.stdout.write(self.style.WARNING(
                        f"  [UNRESOLVED] item {item.pk}: no match for "
                        f"{self._describe(item)!r}"
                    ))
                continue

            resolved += 1
            if not quiet:
                self.stdout.write(f"  [OK] item {item.pk}: {result.title} → {result.url}")
            if not dryRun:
                item.resolvedUrl = result.url
                item.resolvedLabel = result.title
                item.resolvedAt = timezone.now()
                item.save(update_fields=["resolvedUrl", "resolvedLabel", "resolvedAt"])

        verb = "Would resolve" if dryRun else "Resolved"
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: {resolved} | Unresolved: {unresolved} | Errored: {errored} "
            f"(of {len(items)} wiki items)"
        ))

        # Non-zero exit if any item hit an unexpected error so the scheduler
        # surfaces real breakage. An unresolved item (no matching doc) is an
        # expected, benign state — exit 0.
        if errored:
            raise SystemExit(1)

    def _resolve(self, api, item):
        if item.wikiMode == LinkTreeItem.WikiMode.PINNED:
            return WikiLinkResolver.resolvePinned(api, item.pinnedWikiDocId)
        return WikiLinkResolver.resolveLatest(
            api, item.wikiQuery, item.wikiCollectionId or None
        )

    def _describe(self, item) -> str:
        if item.wikiMode == LinkTreeItem.WikiMode.PINNED:
            return f"pinned doc {item.pinnedWikiDocId}"
        return f"query '{item.wikiQuery}'"
