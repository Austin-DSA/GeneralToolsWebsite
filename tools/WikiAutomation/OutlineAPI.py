"""Pure Outline wiki REST client - stdlib only, no Django imports.

Outline exposes an RPC-style API: every call is ``POST {baseUrl}/api/{method}``
with a JSON body and an ``Authorization: Bearer <token>`` header. Responses are
wrapped in an envelope: ``{ "ok": bool, "data": ..., "pagination": {...}, ... }``.

This module is deliberately framework-free so it can be unit-tested by
monkeypatching :meth:`OutlineAPI._call`. The only credential it needs is an API
token, which in Outline acts *as* the user who minted it. The Link Tree uses it
for full-text search over published documents, single-document lookups (see
``searchDocuments`` / ``getDocument``), and one deliberate write: get-or-create
of a document's public share link (``ensurePublishedShareUrl``) so link-tree
buttons never gate readers behind a wiki login.
"""

import dataclasses
import json
import urllib.error
import urllib.request

# Outline caps list/search page size at 100.
_MAX_PAGE_SIZE = 100


@dataclasses.dataclass
class OutlineConfig:
    baseUrl: str  # e.g. "https://wiki.austindsa.org" (no trailing slash required)
    apiToken: str
    timeoutSeconds: int = 30


@dataclasses.dataclass
class OutlineDocument:
    id: str
    title: str
    published: bool  # derived: publishedAt is not None
    collectionId: str | None = None  # for client-side collection scoping
    url: str | None = None  # relative url path, for building absolute links
    publishedAt: str | None = None  # ISO timestamp, for recency sorting
    updatedAt: str | None = None  # ISO timestamp, for recency sorting

    @staticmethod
    def fromApiObject(doc: dict) -> "OutlineDocument":
        return OutlineDocument(
            id=doc["id"],
            title=doc.get("title") or "",
            published=doc.get("publishedAt") is not None,
            collectionId=doc.get("collectionId"),
            url=doc.get("url"),
            publishedAt=doc.get("publishedAt"),
            updatedAt=doc.get("updatedAt"),
        )

    def recencyKey(self) -> str:
        """Sort key for 'newest first' - most recent of updatedAt/publishedAt.

        ISO-8601 timestamps sort correctly as plain strings. Empty string sorts
        last so undated docs never win the 'latest' pick.
        """
        return self.updatedAt or self.publishedAt or ""


@dataclasses.dataclass
class OutlineShare:
    id: str
    url: str  # absolute share URL, e.g. "https://wiki.example.org/s/<urlId>"
    published: bool  # shares only bypass wiki login once published

    @staticmethod
    def fromApiObject(share: dict) -> "OutlineShare":
        return OutlineShare(
            id=share.get("id") or "",
            url=share.get("url") or "",
            published=bool(share.get("published")),
        )


class OutlineAPIError(Exception):
    """Raised on any non-2xx response or transport failure.

    Carries the method, HTTP status (if any), and a truncated response body so
    callers can log a useful diagnostic without leaking the whole payload.
    """

    def __init__(self, method: str, status: int | None, body: str):
        self.method = method
        self.status = status
        self.body = body
        super().__init__(f"Outline API '{method}' failed (status={status}): {body}")


class OutlineAPI:
    def __init__(self, config: OutlineConfig):
        self._config = config

    # --- transport -----------------------------------------------------------

    def _call(self, method: str, payload: dict) -> dict:
        """POST {baseUrl}/api/{method} with a JSON body; return the parsed envelope.

        No retry/backoff: a 429 (Outline returns one with a ``Retry-After``
        header) surfaces as an OutlineAPIError. Acceptable for a low-volume
        daily sweep - the caller treats a failed listing as a fatal error.
        """
        url = f"{self._config.baseUrl.rstrip('/')}/api/{method}"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Authorization", f"Bearer {self._config.apiToken}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(
                request, timeout=self._config.timeoutSeconds
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise OutlineAPIError(method, e.code, body) from e
        except urllib.error.URLError as e:
            raise OutlineAPIError(method, None, str(e.reason)) from e

    # --- documents (read-only) -----------------------------------------------

    def getDocument(self, documentId: str) -> OutlineDocument:
        """Fetch a single document via ``documents.info``.

        Fallback path for when ``text`` is absent/truncated in the list response.
        """
        envelope = self._call("documents.info", {"id": documentId})
        return OutlineDocument.fromApiObject(envelope.get("data") or {})

    def searchDocuments(
        self, query: str, collectionId: str | None = None, limit: int = 25
    ) -> list[OutlineDocument]:
        """Full-text search PUBLISHED documents via ``documents.search``.

        Used by the Link Tree to surface wiki content (e.g. the latest GBM
        agenda). Requires the ``documents.search`` token scope - which the LC
        secretary token deliberately does NOT have, so this runs under a separate
        read-only token (see SecretManager.getOutlineReadConfig).

        ``documents.search`` returns a list of result objects wrapping the
        document under a ``document`` key. ``collectionId`` scopes the search when
        provided. Returns OutlineDocuments in Outline's relevance order; callers
        pick by recency.
        """
        payload: dict = {"query": query, "limit": min(limit, _MAX_PAGE_SIZE)}
        if collectionId:
            payload["collectionId"] = collectionId
        envelope = self._call("documents.search", payload)
        results = envelope.get("data") or []
        documents: list[OutlineDocument] = []
        for result in results:
            # Search results nest the document; older instances may inline it.
            docObject = result.get("document") if isinstance(result, dict) else None
            documents.append(OutlineDocument.fromApiObject(docObject or result))
        return documents

    # --- shares (the one write: public share links) ----------------------------

    def ensurePublishedShareUrl(self, documentId: str) -> str:
        """Get-or-create the document's share link and make sure it is published.

        ``shares.create`` is get-or-create in Outline: it returns the existing
        share when one already exists. A share only bypasses wiki login once its
        ``published`` flag is set, so unpublished shares are published here via
        ``shares.update``. Anything surfaced on a link tree is by definition
        meant to be readable without an account - historically docs were
        sometimes "public" in intent but never actually shared, a mistake this
        removes.

        Requires the ``shares.create`` and ``shares.update`` token scopes.
        Raises OutlineAPIError on any failure; callers fall back to the direct
        document URL (see ``WikiLinkResolver._publicUrl``).
        """
        envelope = self._call("shares.create", {"documentId": documentId})
        share = OutlineShare.fromApiObject(envelope.get("data") or {})
        if not share.url:
            raise OutlineAPIError("shares.create", None, "response had no share url")
        if not share.published:
            self._call("shares.update", {"id": share.id, "published": True})
        return share.url

    def absoluteDocUrl(self, urlPath: str | None, documentId: str) -> str:
        """Absolute URL for a document, for building Link Tree button targets.

        Accepts the relative ``url`` path Outline returns (e.g. ``/doc/slug``)
        with the document id as a fallback.
        """
        base = self._config.baseUrl.rstrip("/")
        if urlPath:
            return f"{base}{urlPath}"
        return f"{base}/doc/{documentId}"
