"""Ingest historical "Austin Membership List" zips into MembershipSnapshot rows.

This is the membership-list ingest's imperative core: for each list (a zip
containing one national-export CSV), it strips PII, tallies retention counts
(good standing / member / lapsed / total), and upserts one MembershipSnapshot
row per list, keyed by the list's own date. The accumulated series across all
runs IS the "bleeding curve".

Two sources of zips:

- ``--from-dir <folder>`` - read every ``*.zip`` in a local folder instead of
  email. This is both the offline test path (synthetic zips) and the path
  Garrigan uses for the one-time historical backfill if he exports the inbox
  to a folder instead of letting this command pull it live.
- (default) the ``austindsalistbot`` Gmail inbox, via
  SecretManager.getMembershipBotEmailConfig(). Gated on that optional secret -
  unconfigured means Garrigan hasn't filled in the app password yet, so this
  warns and exits 0 rather than breaking the deploy (mirrors
  sync_link_tree_wiki.py's OutlineReadConfig gate).

PII posture: raw list rows are only ever read into memory / a temp working
dir to compute counts. Only the aggregate counts are persisted
(MembershipSnapshot has no PII columns) - see tools/MembershipList/README.md.

Idempotency: MembershipSnapshot.listDate is unique, and this command always
update_or_create()s on it, so re-running (the whole backfill, or a single
month twice) overwrites instead of duplicating. The live-email path never
marks messages as read, so it's also safe to re-run against the inbox.

Run from the repo root:
    python manage.py ingest_membership_lists --from-dir <folder-of-zips> [--dry-run] [--quiet]
    python manage.py ingest_membership_lists [--dry-run] [--quiet]   # live email
"""

import datetime
import glob
import logging
import os
import re
import tempfile
import zipfile

from django.core.management.base import BaseCommand

from tools.EmailApi.EmailApi import EmailAccount, EmailApiException
from tools.MembershipList import RetentionCounter
from tools.models import MembershipSnapshot
from tools.SecretManager import SecretManager

logger = logging.getLogger(__name__)


class Constants:
    # Mirrors ListManagement/processNewMembers.py Constants - copied, not
    # imported (that repo is independent; see its CLAUDE.md).
    EXPECTED_EMAIL_SUBJECT = "Austin Membership List"
    MEMBERSHIP_LIST_DOWNLOAD_EMAIL = "no-reply@actionkit.com"
    EXPECTED_LIST_ATTACHMENT_NAME = "austin_membership_list.zip"
    DOWNLOAD_ZIP_LIST_MEMBER = "austin_membership_list.csv"


_FILENAME_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class IngestError(Exception):
    pass


class Command(BaseCommand):
    help = (
        "Ingest 'Austin Membership List' zips (from the austindsalistbot inbox, "
        "or a local --from-dir folder) into MembershipSnapshot rows - the "
        "membership bleeding curve's data points."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and report counts only; do not write MembershipSnapshot rows.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-list lines (the summary still prints).",
        )
        parser.add_argument(
            "--from-dir",
            default=None,
            help=(
                "Ingest every *.zip in this local folder instead of the email "
                "inbox. Use for offline testing (synthetic zips) or a manual "
                "backfill from an exported folder."
            ),
        )

    def handle(self, *args, **options):
        dryRun = options["dry_run"]
        quiet = options["quiet"]
        fromDir = options["from_dir"]

        try:
            zipEntries = self._collectZips(fromDir, quiet)
        except _SkipCommand:
            return

        if dryRun and not quiet:
            self.stdout.write(self.style.WARNING(
                "DRY RUN - no MembershipSnapshot rows will be written."
            ))

        processed = 0
        errored = 0
        allUnknownColumns = set()

        for messageDate, zipPath in zipEntries:
            try:
                result = self._ingestOneZip(zipPath, messageDate)
            except IngestError as e:
                errored += 1
                logger.error("Failed to ingest %s: %s", zipPath, e)
                if not quiet:
                    self.stderr.write(self.style.ERROR(
                        f"  [ERROR] {os.path.basename(zipPath)}: {e}"
                    ))
                continue
            except Exception:
                errored += 1
                logger.exception("Unexpected error ingesting %s", zipPath)
                if not quiet:
                    self.stderr.write(self.style.ERROR(
                        f"  [ERROR] {os.path.basename(zipPath)}: unexpected error, see logs"
                    ))
                continue

            counts, unknownColumns = result
            allUnknownColumns.update(unknownColumns)

            if not dryRun:
                MembershipSnapshot.objects.update_or_create(
                    listDate=counts.listDate,
                    defaults=dict(
                        goodStanding=counts.goodStanding,
                        member=counts.member,
                        lapsed=counts.lapsed,
                        total=counts.total,
                        sourceEmailDate=messageDate,
                    ),
                )
            processed += 1
            if not quiet:
                verb = "Would ingest" if dryRun else "Ingested"
                self.stdout.write(
                    f"  [OK] {os.path.basename(zipPath)} -> listDate={counts.listDate} "
                    f"({verb.lower()}) good={counts.goodStanding} member={counts.member} "
                    f"lapsed={counts.lapsed} total={counts.total}"
                )

        self.stdout.write(self.style.SUCCESS(
            f"Processed: {processed} | Errored: {errored} (of {len(zipEntries)} zips)"
        ))
        if allUnknownColumns:
            self.stdout.write(self.style.WARNING(
                "Unrecognized columns seen (excluded from archive, list still "
                "processed): " + ", ".join(sorted(allUnknownColumns))
            ))

        if errored:
            raise SystemExit(1)

    # --- zip collection ------------------------------------------------

    def _collectZips(self, fromDir, quiet):
        """Returns [(messageDate_or_None, zipPath), ...] ascending by date
        where known (from-dir zips with no derivable date sort last, by name)."""
        if fromDir is not None:
            paths = sorted(glob.glob(os.path.join(fromDir, "*.zip")))
            if not quiet:
                self.stdout.write(f"Found {len(paths)} zip(s) in {fromDir}")
            return [(None, p) for p in paths]

        config = SecretManager.getMembershipBotEmailConfig()
        if config is None:
            # Optional secret not configured yet (Garrigan hasn't filled in
            # the austindsalistbot app password) - benign, exit 0. Mirrors
            # sync_link_tree_wiki.py's OutlineReadConfig gate.
            self.stdout.write(self.style.WARNING(
                "Membership bot inbox credentials not configured "
                "(MembershipBotEmailUsername / MembershipBotEmailPassword) - "
                "skipping email ingest. Use --from-dir to test against local "
                "zips instead."
            ))
            raise _SkipCommand()

        username, password = config
        try:
            account = EmailAccount(username, password)
        except Exception as e:
            self.stderr.write(self.style.ERROR(
                f"FATAL: could not connect to membership bot inbox: {e}"
            ))
            raise SystemExit(1)

        # A plain mkdtemp (not a `with` block) - the downloaded zips need to
        # outlive this method, since each is processed one at a time by the
        # caller after we return. Nothing else cleans this up, but it's a
        # one-time historical backfill / a daily scheduled run, not something
        # that needs to be spotless; os temp dirs get swept by the OS/container
        # lifecycle anyway.
        downloadDir = tempfile.mkdtemp(prefix="membership-list-download-")
        try:
            entries = account.downloadAllZipAttachmentsFrom(
                fromAddress=Constants.MEMBERSHIP_LIST_DOWNLOAD_EMAIL,
                subjectContaining=Constants.EXPECTED_EMAIL_SUBJECT,
                downloadDir=downloadDir,
                expectedFileName=Constants.EXPECTED_LIST_ATTACHMENT_NAME,
            )
        except EmailApiException as e:
            self.stderr.write(self.style.ERROR(f"FATAL: email fetch failed: {e}"))
            raise SystemExit(1)
        if not quiet:
            self.stdout.write(f"Found {len(entries)} email(s) with a membership list attachment")
        return entries

    # --- per-zip ingest --------------------------------------------------

    def _ingestOneZip(self, zipPath, messageDate):
        """Returns (RetentionCounts, unknownColumns) or raises IngestError."""
        with tempfile.TemporaryDirectory(prefix="membership-list-extract-") as extractDir:
            try:
                with zipfile.ZipFile(zipPath) as z:
                    memberNames = [
                        n for n in z.namelist()
                        if os.path.basename(n) == Constants.DOWNLOAD_ZIP_LIST_MEMBER
                    ]
                    if not memberNames:
                        raise IngestError(
                            f"zip does not contain {Constants.DOWNLOAD_ZIP_LIST_MEMBER}"
                        )
                    z.extract(memberNames[0], path=extractDir)
                    csvPath = os.path.join(extractDir, memberNames[0])
            except zipfile.BadZipFile as e:
                raise IngestError(f"not a valid zip file: {e}")

            cols, rows = RetentionCounter.readCSV(csvPath)
            columnCheck = RetentionCounter.checkForNewCols(cols)

            listDate = (
                RetentionCounter.extractListDateFromRows(cols, rows)
                or self._dateFromFilename(zipPath)
                or (messageDate.date() if messageDate is not None else None)
            )
            if listDate is None:
                raise IngestError(
                    "could not determine the list's date from its list_date "
                    "column, filename, or source email date"
                )

            try:
                counts = RetentionCounter.processRetentionData(cols, rows, listDate)
            except RetentionCounter.RetentionCounterException as e:
                raise IngestError(str(e))

            return counts, columnCheck.unknownColumns

    def _dateFromFilename(self, zipPath):
        match = _FILENAME_DATE_RE.search(os.path.basename(zipPath))
        if not match:
            return None
        try:
            return datetime.date.fromisoformat(match.group(1))
        except ValueError:
            return None


class _SkipCommand(Exception):
    """Internal signal: the optional inbox creds aren't configured, exit 0."""
