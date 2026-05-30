"""Leadership-Committee note publishing driver.

Mirrors the ``EventAutomationDriver`` philosophy: :func:`sweep` is exception-safe
— it never raises to the caller; instead it collects a per-document outcome into
a :class:`SweepResult`. The command layer wires in the real Outline client, the
email notifier, and the "have we already warned about this note?" check; this
module stays Django-free and unit-testable.

Safety model (see plan R2): there is no reliable machine marker for an executive
session, so we use a deliberately HIGH-RECALL / low-precision keyword scan. A
false positive merely means a human publishes manually; a false negative leaks
confidential content. When in doubt, the note is HELD, never published.
"""

import dataclasses
import enum
import logging
import re
from typing import Callable

from .OutlineAPI import OutlineAPI, OutlineAPIError, OutlineDocument

logger = logging.getLogger(__name__)

# High-recall default keyword list, overridable via the LCExecKeywords secret.
# Kept deliberately tight — only terms that actually denote a closed/executive
# session for this chapter. Looser/jargon terms ("confidential", "in camera")
# were left out as low-yield and prone to incidental false holds; re-add them
# via LCExecKeywords if you want maximum recall.
DEFAULT_KEYWORDS = [
    "executive session",
    "closed session",
    "exec session",
    "personnel matter",
    "legal counsel",
]

# LC meeting notes follow the title convention "<YYYY-MM-DD> LC Minutes" (also
# annual rollups like "2026 LC Minutes"). We match the "LC Minutes" stem
# case-insensitively. Overridable via the LCNotesTitlePattern secret.
# This title filter is how we identify LC notes: drafts in Outline have no
# collection, so we cannot scope by collection.
DEFAULT_TITLE_PATTERN = r"lc minutes"


def findExecSessionHits(text: str, keywords: list[str]) -> list[str]:
    """Case-insensitive substring match; return the keywords that matched."""
    haystack = (text or "").lower()
    return [keyword for keyword in keywords if keyword.lower() in haystack]


def matchesNotePattern(title: str, titlePattern: str) -> bool:
    """Case-insensitive regex search of the draft title against the convention."""
    return re.search(titlePattern, title or "", re.IGNORECASE) is not None


class Outcome(enum.Enum):
    PUBLISHED = "published"
    HELD_EXEC = "held_exec_session"  # keyword hit → not published; author may be emailed
    ERROR = "error"  # API error on this doc; skipped, sweep continues


@dataclasses.dataclass
class NoteResult:
    docId: str
    title: str
    url: str | None
    outcome: Outcome
    matchedKeywords: list[str] = dataclasses.field(default_factory=list)
    authorEmail: str | None = None
    errorStr: str | None = None
    notificationSent: bool = False  # True if an email was sent for this note this run


@dataclasses.dataclass
class SweepResult:
    published: list[NoteResult] = dataclasses.field(default_factory=list)
    held: list[NoteResult] = dataclasses.field(default_factory=list)
    errored: list[NoteResult] = dataclasses.field(default_factory=list)
    # Set only if the *listing* step itself failed (nothing could be swept).
    fatalError: str | None = None


# Injected callables (the command supplies real implementations):
#   notifier(note)            -> bool   send the held-note email; True on success
#   isFirstNotification(docId)-> bool   True if we should email about this held note now
Notifier = Callable[[NoteResult], bool]
IsFirstNotification = Callable[[str], bool]


def sweep(
    api: OutlineAPI,
    titlePattern: str,
    keywords: list[str],
    publishEnabled: bool,
    notifier: Notifier,
    isFirstNotification: IsFirstNotification,
) -> SweepResult:
    """Sweep the author's drafts: publish clean LC notes, hold flagged ones.

    Args:
        api: Outline client.
        titlePattern: regex selecting which drafts are LC notes (matched against
            the title, case-insensitively). Drafts have no collection in Outline,
            so the title is how we identify LC notes.
        keywords: exec-session keywords to scan for.
        publishEnabled: when False (``--dry-run``), classify but never publish.
        notifier: sends the held-note email; called only when isFirstNotification
            returns True for that doc.
        isFirstNotification: returns True if the author should be (re-)notified
            about this held note now (first detection, or weekly reminder due).
    """
    result = SweepResult()

    # Validate the title pattern up front so a bad config value (invalid regex)
    # is a clean fatal error, not an unhandled re.error that crashes the command.
    try:
        re.compile(titlePattern)
    except re.error as e:
        logger.error("LC sweep: invalid title pattern %r: %s", titlePattern, e)
        result.fatalError = f"Invalid LC notes title pattern {titlePattern!r}: {e}"
        return result

    try:
        allDrafts = api.listDrafts()
    except OutlineAPIError as e:
        logger.error("LC sweep: could not list drafts: %s", e)
        result.fatalError = str(e)
        return result

    # Drafts have no collection, so select LC notes by title convention.
    drafts = [d for d in allDrafts if matchesNotePattern(d.title, titlePattern)]

    for draft in drafts:
        try:
            _processDraft(
                api, draft, keywords, publishEnabled, notifier, isFirstNotification, result
            )
        except OutlineAPIError as e:
            logger.error("LC sweep: error processing doc %s: %s", draft.id, e)
            result.errored.append(
                NoteResult(
                    docId=draft.id,
                    title=draft.title,
                    url=draft.url,
                    outcome=Outcome.ERROR,
                    authorEmail=draft.authorEmail,
                    errorStr=str(e),
                )
            )
        except Exception as e:  # defensive: a single bad doc must not abort the sweep
            logger.exception("LC sweep: unexpected error processing doc %s", draft.id)
            result.errored.append(
                NoteResult(
                    docId=draft.id,
                    title=draft.title,
                    url=draft.url,
                    outcome=Outcome.ERROR,
                    authorEmail=draft.authorEmail,
                    errorStr=str(e),
                )
            )

    return result


def _processDraft(
    api: OutlineAPI,
    draft: OutlineDocument,
    keywords: list[str],
    publishEnabled: bool,
    notifier: Notifier,
    isFirstNotification: IsFirstNotification,
    result: SweepResult,
) -> None:
    # Resolve author email: prefer the embedded value, fall back to users.info.
    authorEmail = draft.authorEmail
    if authorEmail is None and draft.authorId:
        authorEmail = api.getUserEmail(draft.authorId)

    # Resolve body: prefer the embedded text, fall back to documents.info.
    body = draft.text
    if not body:
        body = api.getDocument(draft.id).text

    hits = findExecSessionHits(f"{draft.title or ''}\n{body or ''}", keywords)

    if hits:
        note = NoteResult(
            docId=draft.id,
            title=draft.title,
            url=draft.url,
            outcome=Outcome.HELD_EXEC,
            matchedKeywords=hits,
            authorEmail=authorEmail,
        )
        if isFirstNotification(draft.id):
            note.notificationSent = notifier(note)
        result.held.append(note)
        return

    note = NoteResult(
        docId=draft.id,
        title=draft.title,
        url=draft.url,
        outcome=Outcome.PUBLISHED,
        authorEmail=authorEmail,
    )
    if publishEnabled:
        api.publishDocument(draft.id)
    result.published.append(note)
