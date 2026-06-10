"""Auto-publish Leadership Committee meeting notes from the Outline wiki.

The secretary writes LC notes as drafts in Outline and often forgets to publish
them. This command sweeps the secretary's drafts (selected by title — drafts
have no collection in Outline) and:

  * publishes any draft with ZERO executive-session keyword hits, and
  * HOLDS any draft that has a keyword hit (does not publish) and emails its
    author that it needs manual handling — once on first detection, then at most
    once per week (tracked in the NotifiedHeldNote model).

It always prints a report and emails a run summary to the fallback address.

IMPORTANT — single-token visibility: in Outline a draft is private to its
author, so this command only sees drafts created by the user whose API token is
configured. Run it under the secretary's token. If LC notes ever have multiple
authors, other authors' drafts are invisible to this sweep (out of scope).

Schedule via host cron / Windows Task Scheduler — there is no in-process
scheduler. Start with --dry-run for the first week, then drop the flag once the
report output is trusted.

Run from the repo root:
    python manage.py publish_lc_notes [--dry-run] [--no-email] [--quiet]
"""

import datetime
import logging

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from tools.EmailApi import EmailApi
from tools.models import NotifiedHeldNote
from tools.SecretManager import SecretManager
from tools.WikiAutomation import LCNotePublisher
from tools.WikiAutomation.LCNotePublisher import NoteResult, Outcome
from tools.WikiAutomation.OutlineAPI import OutlineAPI

logger = logging.getLogger(__name__)

# Re-email the author of a still-held note at most this often.
REMINDER_INTERVAL_DAYS = 7


class Command(BaseCommand):
    help = "Auto-publish clean LC wiki drafts; hold and report ones that may contain an executive session."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Classify and report only. Do not publish anything or send emails.",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Publish/hold as normal but send NO emails — neither author "
            "notifications nor the operator run-summary. The report still prints to stdout.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-document stdout lines (the summary is still printed).",
        )

    def handle(self, *args, **options):
        dryRun = options["dry_run"]
        noEmail = options["no_email"] or dryRun
        quiet = options["quiet"]

        outlineConfig = SecretManager.getOutlineConfig()
        lcConfig = SecretManager.getLCNotesConfig()
        api = OutlineAPI(outlineConfig)

        if dryRun:
            self.stdout.write(self.style.WARNING("DRY RUN — no documents will be published, no emails sent."))

        def isFirstNotification(docId: str) -> bool:
            if noEmail:
                return False  # never notify in dry-run / no-email modes
            record = NotifiedHeldNote.objects.filter(docId=docId).first()
            if record is None:
                return True
            # Compare timedeltas directly so the boundary is exactly N days
            # (timedelta.days floors, which would stretch the interval to [7, 8)).
            elapsed = timezone.now() - record.lastNotifiedAt
            return elapsed >= datetime.timedelta(days=REMINDER_INTERVAL_DAYS)

        def notifier(note: NoteResult) -> bool:
            recipient = note.authorEmail or lcConfig.fallbackEmail
            if not recipient:
                logger.warning("Held note %s: no author email and no fallback configured.", note.docId)
                return False
            sent = self._sendHeldNoteEmail(api, note, recipient)
            if sent:
                self._recordNotification(note)
            return sent

        result = LCNotePublisher.sweep(
            api=api,
            titlePattern=lcConfig.titlePattern,
            keywords=lcConfig.keywords,
            publishEnabled=not dryRun,
            notifier=notifier,
            isFirstNotification=isFirstNotification,
        )

        self._renderReport(api, result, dryRun=dryRun, quiet=quiet)

        if not noEmail:
            self._emailRunSummary(result, lcConfig.fallbackEmail, dryRun=dryRun)

        # Non-zero exit on a fatal listing failure or any per-doc error so the
        # scheduler surfaces real breakage. Held notes are expected — exit 0.
        if result.fatalError:
            raise SystemExit(1)
        if result.errored:
            raise SystemExit(1)

    # --- notifications -------------------------------------------------------

    def _sendHeldNoteEmail(self, api: OutlineAPI, note: NoteResult, recipient: str) -> bool:
        url = api.absoluteDocUrl(note.url, note.docId)
        subject = f"[Action needed] LC note held — possible executive session: {note.title}"
        body = (
            f"The automated LC-notes publisher found a draft that may contain "
            f"executive-session content, so it was NOT published automatically.\n\n"
            f"Title: {note.title}\n"
            f"Link: {url}\n"
            f"Matched keywords: {', '.join(note.matchedKeywords)}\n\n"
            f"Please review and publish it manually (redacting any closed-session "
            f"content first). If this was a false alarm, just publish the note in "
            f"Outline and it will stop appearing in this sweep.\n"
        )
        try:
            EmailApi.sendEmailFromWebsiteAccount(
                toAddress=recipient, subject=subject, messageText=body
            )
            return True
        except Exception:
            # SMTP is flagged flaky in this codebase; never let it abort the sweep.
            logger.exception("Failed to send held-note email for doc %s", note.docId)
            return False

    def _recordNotification(self, note: NoteResult) -> None:
        # update_or_create is race-safe against the docId unique constraint (it
        # retries on IntegrityError), so overlapping runs can't crash here or
        # leave the row uncreated. The save() refreshes lastNotifiedAt (auto_now).
        record, created = NotifiedHeldNote.objects.update_or_create(
            docId=note.docId,
            defaults={"title": note.title or ""},
        )
        if not created:
            # Atomic increment avoids the lost-update problem on overlapping runs.
            NotifiedHeldNote.objects.filter(pk=record.pk).update(notifyCount=F("notifyCount") + 1)

    def _emailRunSummary(self, result, fallbackEmail: str, dryRun: bool) -> None:
        if not fallbackEmail:
            return
        try:
            EmailApi.sendEmailFromWebsiteAccount(
                toAddress=fallbackEmail,
                subject=self._summarySubject(result, dryRun),
                messageText=self._summaryText(result, dryRun),
            )
        except Exception:
            logger.exception("Failed to send run-summary email")

    # --- reporting -----------------------------------------------------------

    def _renderReport(self, api: OutlineAPI, result, dryRun: bool, quiet: bool) -> None:
        if result.fatalError:
            self.stderr.write(self.style.ERROR(f"FATAL: could not list LC drafts: {result.fatalError}"))
            return

        verb = "Would publish" if dryRun else "Published"
        self.stdout.write(self.style.SUCCESS(
            f"{verb}: {len(result.published)} | Held: {len(result.held)} | Errored: {len(result.errored)}"
        ))

        if quiet:
            return

        for note in result.published:
            self.stdout.write(f"  [{verb.upper()}] {note.title} ({api.absoluteDocUrl(note.url, note.docId)})")
        for note in result.held:
            if not note.notificationSent:
                flag = "not emailed this run"
            elif not note.authorEmail:
                flag = "emailed fallback — author email unresolved"
            else:
                flag = "emailed author"
            self.stdout.write(self.style.WARNING(
                f"  [HELD] {note.title} — keywords: {', '.join(note.matchedKeywords)} [{flag}]"
            ))
        for note in result.errored:
            self.stderr.write(self.style.ERROR(f"  [ERROR] {note.title or note.docId} ({note.docId}): {note.errorStr}"))

    def _summarySubject(self, result, dryRun: bool) -> str:
        prefix = "[DRY RUN] " if dryRun else ""
        if result.fatalError:
            return f"{prefix}LC notes sweep FAILED"
        return (
            f"{prefix}LC notes sweep: {len(result.published)} published, "
            f"{len(result.held)} held, {len(result.errored)} errored"
        )

    def _summaryText(self, result, dryRun: bool) -> str:
        if result.fatalError:
            return f"The LC notes sweep could not list drafts and did nothing:\n\n{result.fatalError}\n"

        lines = []
        if dryRun:
            lines.append("DRY RUN — nothing was actually published or emailed.\n")

        lines.append(f"Published ({len(result.published)}):")
        lines += [f"  - {n.title}" for n in result.published] or ["  (none)"]
        lines.append("")
        lines.append(f"Held — possible executive session ({len(result.held)}):")
        lines += [
            f"  - {n.title} [keywords: {', '.join(n.matchedKeywords)}]"
            + ("  (author email unresolved — sent to fallback)" if n.notificationSent and not n.authorEmail else "")
            for n in result.held
        ] or ["  (none)"]
        lines.append("")
        lines.append(f"Errored ({len(result.errored)}):")
        lines += [f"  - {n.title or n.docId} ({n.docId}): {n.errorStr}" for n in result.errored] or ["  (none)"]
        return "\n".join(lines) + "\n"
