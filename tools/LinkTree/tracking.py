"""Privacy-first click/scan tracking for the Link Tree.

Austin DSA is a political org wary of surveillance, so this deliberately stores
**no raw IP address**. Instead it records a ``visitorHash`` - a salted, daily-
rotating digest of (IP + user-agent) that is good only for rough *same-day*
unique counts and is useless as a cross-day identifier (the salt rotates every
day, and the IP is never persisted).

The pure helpers (``visitorHash``, ``uaFamily``, ``referrerHost``,
``clientIpFromMeta``) are framework-light and unit-testable without a request.
``recordEvent`` performs the Django write and is deliberately exception-safe -
mirroring the "a dropped email must never fail a publish" ethos elsewhere in the
codebase, a tracking failure must never break a redirect.
"""

import datetime
import hashlib
import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def clientIpFromMeta(meta: dict) -> str:
    """Best-effort client IP from request.META.

    Prod sits behind nginx, so honor the first hop of X-Forwarded-For; fall back
    to REMOTE_ADDR. Returns "" if neither is present. The value is used only to
    compute visitorHash and is never stored.
    """
    forwarded = meta.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return meta.get("REMOTE_ADDR", "") or ""


def visitorHash(ip: str, userAgent: str, salt: str, day: datetime.date | None = None) -> str:
    """Salted, daily-rotating 16-char digest of IP+UA. No raw IP is retained.

    Same visitor on the same UTC day → same hash (rough uniques); next day → a
    different hash. ``salt`` should be a server secret (settings.SECRET_KEY).
    """
    if day is None:
        day = datetime.datetime.now(datetime.UTC).date()
    material = f"{day.isoformat()}:{salt}:{ip}:{userAgent}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:16]


def uaFamily(userAgent: str) -> str:
    """Coarse, low-cardinality UA family (no fingerprinting). e.g. 'mobile-safari'."""
    ua = (userAgent or "").lower()
    if not ua:
        return ""
    platform = "mobile" if any(t in ua for t in ("mobile", "iphone", "android")) else "desktop"
    if "edg/" in ua or "edge" in ua:
        browser = "edge"
    elif "chrome" in ua or "crios" in ua:
        browser = "chrome"
    elif "firefox" in ua or "fxios" in ua:
        browser = "firefox"
    elif "safari" in ua:
        browser = "safari"
    elif "bot" in ua or "crawler" in ua or "spider" in ua:
        return "bot"
    else:
        browser = "other"
    return f"{platform}-{browser}"


def referrerHost(referrer: str) -> str:
    """Host portion of the referrer only - never the full URL (no path/query)."""
    if not referrer:
        return ""
    try:
        return urlsplit(referrer).hostname or ""
    except ValueError:
        return ""


def recordEvent(request, *, source, tree=None, item=None, qr=None, destinationUrl: str = "") -> None:
    """Write a LinkEvent for this request. Never raises.

    Imported lazily so this module stays importable without Django configured
    (keeps the pure helpers unit-testable in isolation).
    """
    try:
        from django.conf import settings
        from ..models import LinkEvent

        meta = request.META
        ip = clientIpFromMeta(meta)
        ua = meta.get("HTTP_USER_AGENT", "")
        # A dedicated salt so the chapter can rotate Django's SECRET_KEY without
        # silently resetting visitor-uniqueness continuity. Defaults to SECRET_KEY.
        salt = getattr(settings, "LINK_TRACKING_SALT", "") or settings.SECRET_KEY
        LinkEvent.objects.create(
            tree=tree,
            item=item,
            qr=qr,
            source=source,
            destinationUrl=destinationUrl or "",
            visitorHash=visitorHash(ip, ua, salt),
            uaFamily=uaFamily(ua),
            referrerHost=referrerHost(meta.get("HTTP_REFERER", "")),
        )
    except Exception:
        # Tracking is best-effort; a logging failure must never break a redirect.
        logger.exception("Failed to record LinkEvent (source=%s)", source)
