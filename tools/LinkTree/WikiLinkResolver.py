"""Resolve Link Tree wiki items to live Outline documents.

Django-free so it can be unit-tested by injecting an OutlineAPI whose ``_call``
is monkeypatched (see ``_FakeOutline`` in ``tools/tests.py``). The
``sync_link_tree_wiki`` management command wires this to the DB: it reads each
WIKI item's fields, calls the resolver, and caches the returned url/title back
onto the item so the public page never has to hit Outline at request time.
"""

import dataclasses
import logging

from ..WikiAutomation.OutlineAPI import OutlineAPI, OutlineAPIError, OutlineDocument

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ResolveResult:
    url: str  # absolute URL to the wiki document (published share link when possible)
    title: str  # the document's current title


def _publicUrl(api: OutlineAPI, doc: OutlineDocument, createShares: bool = True) -> str:
    """Best public URL for a resolved doc: its published share link if possible.

    Share links open without a wiki login - a GBM agenda on a public link tree
    must be readable by non-members. Anything surfaced on a link tree is assumed
    shareable, so the share is created (and published) on demand. On any share
    failure (missing token scope, sharing disabled, transient error) fall back
    to the direct ``/doc/...`` URL - no worse than not sharing at all.

    ``createShares=False`` skips Outline share calls entirely and returns the
    direct URL - the dry-run path, which must be side-effect-free.
    """
    if not createShares:
        return api.absoluteDocUrl(doc.url, doc.id)
    try:
        return api.ensurePublishedShareUrl(doc.id)
    except OutlineAPIError:
        logger.exception(
            "Could not ensure a share link for doc %s; falling back to the direct URL",
            doc.id,
        )
        return api.absoluteDocUrl(doc.url, doc.id)


def resolveLatest(
    api: OutlineAPI,
    query: str,
    collectionId: str | None = None,
    createShares: bool = True,
) -> ResolveResult | None:
    """Newest PUBLISHED doc whose title contains ``query`` (case-insensitive).

    Outline's full-text search can match on body too, so we additionally require
    the query to appear in the title (the GBM agenda / LC minutes convention puts
    a stable phrase in the title). Among the matches we pick the most recently
    updated. Returns None if nothing matches or the search fails.
    """
    if not query:
        return None
    try:
        results = api.searchDocuments(query, collectionId=collectionId)
    except OutlineAPIError:
        logger.exception("Outline search failed for query %r", query)
        return None

    needle = query.casefold()
    matches = [
        doc for doc in results
        if doc.published and needle in (doc.title or "").casefold()
    ]
    if not matches:
        return None

    newest = max(matches, key=lambda doc: doc.recencyKey())
    return ResolveResult(url=_publicUrl(api, newest, createShares), title=newest.title)


def resolvePinned(
    api: OutlineAPI, documentId: str, createShares: bool = True
) -> ResolveResult | None:
    """Resolve one specific document by id to its current url/title.

    Returns None on any failure so a broken pin can't crash the whole sweep.
    """
    if not documentId:
        return None
    try:
        doc = api.getDocument(documentId)
    except OutlineAPIError:
        logger.exception("Outline getDocument failed for id %s", documentId)
        return None
    if not doc.id:
        return None
    return ResolveResult(url=_publicUrl(api, doc, createShares), title=doc.title)
