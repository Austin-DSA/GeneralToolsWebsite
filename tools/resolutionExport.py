"""The Echo -> Bylaws-Resolutions handoff.

Echo is the canonical store of an adopted resolution. This renders that record
in the exact markdown shape the Bylaws-Resolutions repo uses
(``Resolutions/<year>/<slug>.md``) so the Secretary can commit a mirror by hand.
The handoff is one-directional: Echo never reads back from the repo.

The repo's existing files vary in their "Passed:" line punctuation; we emit the
most common shape ("Passed: M/D/YYYY - Y Yes - N No - A Abstain"). See the
knowledge bundle's resolution-record note on that inconsistency.
"""
from .models import Resolution


def _adoptedDate(resolution: Resolution):
    """The chapter-local date the resolution took effect, for the archive record.
    Prefers the stored effectiveDate (already chapter-local); falls back to
    localizing decidedAt. decidedAt is UTC, so using it raw would file a
    late-evening Central adoption under the wrong day (or year, at the boundary)
    in the permanent record."""
    if resolution.effectiveDate is not None:
        return resolution.effectiveDate
    return resolution.decidedDateLocal()


def repoFilename(resolution: Resolution) -> str:
    """The suggested ``<year>/<slug>.md`` path within the repo's Resolutions/ dir."""
    adopted = _adoptedDate(resolution)
    yearStr = str(adopted.year) if adopted is not None else "unknown"
    slug = resolution.slug or resolution._generateSlug()
    return f"{yearStr}/{slug}.md"


def resolutionRepoMarkdown(resolution: Resolution) -> str:
    """The adopted resolution rendered as a Bylaws-Resolutions markdown file."""
    lines = [f"# {resolution.title}"]

    adopted = _adoptedDate(resolution)
    if adopted is not None:
        datePart = f"{adopted.month}/{adopted.day}/{adopted.year}"
        if resolution.votesYes is not None:
            lines.append(
                f"Passed: {datePart} - {resolution.votesYes} Yes - "
                f"{resolution.votesNo} No - {resolution.votesAbstain} Abstain"
            )
        else:
            lines.append(f"Passed: {datePart}")

    lines += ["", "## Resolution Text", "", resolution.text.strip(), ""]
    return "\n".join(lines)
