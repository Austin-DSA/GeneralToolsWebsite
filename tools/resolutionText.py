"""Resolution text helpers: the integrity hash and a safe markdown renderer.

Both are framework-free (no Django imports) and dependency-free. We deliberately
do NOT pull in python-markdown + bleach here: the codebase is intentionally lean
(stdlib urllib over requests, etc.), and an un-sanitized markdown library on
member-authored text that the Secretary then views is an XSS footgun. This small
renderer supports the constructs resolutions actually use (headings, emphasis,
lists, blockquotes, links, inline code, rules) and is XSS-safe by construction:
every line's content is HTML-escaped before any tag we emit is inserted, and link
hrefs are scheme-checked. Anything unsupported degrades to escaped plain text.

If a fuller renderer is ever wanted, swap renderMarkdown() for python-markdown +
bleach behind this same function signature.
"""
import hashlib
import html
import re


def normalizedTextHash(text: str) -> str:
    """sha256 of the resolution text, normalized so a cosmetically-identical
    resave does not reset sign-ons.

    Normalization: CRLF -> LF (a Windows resave or a paste with different line
    endings must not change the hash) and strip surrounding whitespace. This is
    the single source of truth for the lock; the model and the edit-comparison
    both call it."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# --- markdown rendering (safe subset) -----------------------------------

_SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\d+\.\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")

_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)")


def _safeHref(url: str) -> str | None:
    """Return an attribute-safe href if the scheme is allowed, else None."""
    candidate = url.strip()
    lowered = candidate.lower()
    allowed = lowered.startswith(_SAFE_LINK_SCHEMES) or candidate.startswith(("/", "#"))
    if not allowed:
        return None
    # The url comes from already-escaped text (& -> &amp;); guard the quote chars
    # that would break out of the attribute.
    return candidate.replace('"', "%22").replace("'", "%27")


def _renderInline(escaped: str) -> str:
    """Apply inline formatting to text that has ALREADY been HTML-escaped."""
    # Code spans first so their contents are not further formatted.
    escaped = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)

    def link(match):
        text, url = match.group(1), match.group(2)
        href = _safeHref(url)
        if href is None:
            return text  # drop the unsafe link, keep the visible text
        return f'<a href="{href}" target="_blank" rel="noopener">{text}</a>'

    escaped = _LINK_RE.sub(link, escaped)
    escaped = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", escaped)
    escaped = _ITALIC_RE.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", escaped)
    return escaped


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def renderMarkdown(text: str) -> str:
    """Render a safe HTML fragment from resolution markdown. Returns "" for empty
    input. The result is safe to mark_safe()."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    htmlParts: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        if line.strip() == "":
            i += 1
            continue

        if _HR_RE.match(line.strip()):
            htmlParts.append("<hr />")
            i += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)  # offset: page owns h1
            content = _renderInline(_esc(heading.group(2).strip()))
            htmlParts.append(f"<h{level}>{content}</h{level}>")
            i += 1
            continue

        if _UL_RE.match(line):
            items = []
            while i < n and _UL_RE.match(lines[i]):
                items.append(_renderInline(_esc(_UL_RE.match(lines[i]).group(1).strip())))
                i += 1
            htmlParts.append("<ul>" + "".join(f"<li>{it}</li>" for it in items) + "</ul>")
            continue

        if _OL_RE.match(line):
            items = []
            while i < n and _OL_RE.match(lines[i]):
                items.append(_renderInline(_esc(_OL_RE.match(lines[i]).group(1).strip())))
                i += 1
            htmlParts.append("<ol>" + "".join(f"<li>{it}</li>" for it in items) + "</ol>")
            continue

        if _BLOCKQUOTE_RE.match(line):
            quoted = []
            while i < n and _BLOCKQUOTE_RE.match(lines[i]):
                quoted.append(_renderInline(_esc(_BLOCKQUOTE_RE.match(lines[i]).group(1).strip())))
                i += 1
            htmlParts.append("<blockquote>" + "<br />".join(quoted) + "</blockquote>")
            continue

        # Paragraph: gather consecutive plain lines until a blank or a block start.
        para = []
        while i < n and lines[i].strip() != "" and not (
            _HR_RE.match(lines[i].strip())
            or _HEADING_RE.match(lines[i])
            or _UL_RE.match(lines[i])
            or _OL_RE.match(lines[i])
            or _BLOCKQUOTE_RE.match(lines[i])
        ):
            para.append(_renderInline(_esc(lines[i].strip())))
            i += 1
        htmlParts.append("<p>" + "<br />".join(para) + "</p>")

    return "".join(htmlParts)
