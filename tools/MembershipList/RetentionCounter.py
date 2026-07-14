"""Obfuscate + count a national membership-list CSV.

Copied and adapted from ``Membership-Engagment-Tools/ListManagement/processNewMembers.py``
and ``Utils.py`` (a sibling, independent repo — copied per that repo's house
rule, never imported; see ``Membership-Engagment-Tools/CLAUDE.md``).

Two changes from the original single-list pipeline, both required to run this
over 5-6 years of historical lists in one backfill instead of one list a week:

1. ``checkForNewCols`` no longer raises on an unknown column. National has
   added/renamed columns over the years; aborting the whole list would mean
   losing that list's data point in the bleeding curve. Policy: **log-and-skip
   unknown columns** — they are simply excluded from the obfuscated archive
   (``archiveAndObfuscate`` already only keeps columns explicitly mapped
   ``True``), and the caller gets back the list of unrecognized columns so it
   can surface a summary at the end of a run. See the package README for the
   full rationale.
2. ``processRetentionData`` returns a ``RetentionCounts`` dataclass instead of
   appending a row to a local CSV file — the caller (the Django management
   command) decides how to persist it (``MembershipSnapshot.update_or_create``).
"""

import csv
import dataclasses
import datetime
import logging
import typing

logger = logging.getLogger(__name__)


class RetentionCounterException(Exception):
    pass


class Constants:
    # --- Column name constants (copied from ListManagement/Utils.py's
    # Utils.Constants.MEMBERSHIP_LIST_COLS) ---
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    STANDING_COL = "membership_status"
    EMAIL_COL = "email"
    LIST_DATE_COL = "list_date"

    class MEMBERSHIP_STATUS:
        LAPSED = "lapsed"
        GOOD_STANDING = "member in good standing"
        MEMBER = "member"

    # Column -> keep-in-obfuscated-archive map. Copied verbatim from
    # ListManagement/processNewMembers.py Constants.COLS_TO_KEEP_FOR_ARCHIVE.
    # True = safe to keep in the obfuscated archive/DB (non-identifying).
    # False = PII, drop it. This dict IS the definition of "obfuscated" for
    # this pipeline — do not add a column mapped True unless it is genuinely
    # non-identifying.
    COLS_TO_KEEP_FOR_ARCHIVE = {
        "prefix": False,
        "mailing_pref": False,
        "actionkit_id": False,
        "first_name": False,
        "middle_name": False,
        "last_name": False,
        "suffix": False,
        "billing_address1": False,
        "billing_address2": False,
        "billing_city": False,
        "billing_state": False,
        "billing_zip": False,
        # TODO Combine mailing addresses
        "mailing_address1": False,
        "address1": False,
        "mailing_address2": False,
        "address2": False,
        "mailing_city": False,
        "city": False,
        "mailing_state": False,
        "state": False,
        "country": False,
        "mailing_zip": True,
        "zip": True,
        "best_phone": False,
        "mobile_phone": False,
        "home_phone": False,
        "work_phone": False,
        "email": False,
        "mail_preference": False,
        "do_not_call": True,
        "p2ptext_optout": True,
        "join_date": True,
        "xdate": True,
        "membership_type": True,
        "monthly_dues_status": True,
        "annual_recurring_dues_status": True,
        "yearly_dues_status": True,
        "membership_status": True,
        "memb_status_letter": True,
        "union_member": True,
        "union_name": True,
        "union_local": True,
        "student_yes_no": True,
        "student_school_name": True,
        "ydsa_chapter": True,
        "dsa_chapter": True,
        "accomodations": True,
        "accommodations": True,
        "race": True,
        "list_date": True,
        "new_members_last_month": True,
        "new_member_past_month": True,
    }


@dataclasses.dataclass
class RetentionCounts:
    """One data point in the bleeding curve — mirrors MembershipSnapshot's
    non-key fields plus the list's own date."""

    listDate: datetime.date
    goodStanding: int
    member: int
    lapsed: int
    total: int


@dataclasses.dataclass
class ColumnCheckResult:
    unknownColumns: list


def readCSV(filename: str):
    """Copied from ListManagement/Utils.py readCSV."""
    rows = []
    cols = None
    with open(filename, "r", newline="", encoding="utf8") as file:
        reader = csv.reader(file)
        for line in reader:
            if cols is None:
                cols = line
            else:
                rows.append(line)
    return cols, rows


def checkForNewCols(cols: list) -> ColumnCheckResult:
    """Log-and-skip unknown columns instead of raising.

    National has renamed/added columns over 5-6 years of lists; a historical
    backfill can't afford to abort a whole list over one unrecognized column.
    Unknown columns are simply excluded from the obfuscated archive (see
    archiveAndObfuscate) and returned here so the caller can print a summary.
    """
    unknownColumns = []
    for c in cols:
        if c not in Constants.COLS_TO_KEEP_FOR_ARCHIVE:
            logger.warning(
                "Column %r is not in COLS_TO_KEEP_FOR_ARCHIVE - excluding it "
                "from the obfuscated archive. This list will still be processed.",
                c,
            )
            unknownColumns.append(c)
    return ColumnCheckResult(unknownColumns=unknownColumns)


def archiveAndObfuscate(cols: list, rows: list):
    """Filter rows down to only the non-PII (True) columns.

    Copied from ListManagement/processNewMembers.py archiveAndObfuscate, minus
    the Google Drive / local-CSV upload paths — callers decide what to do with
    the returned (newCols, archiveRows).
    """
    colIndexes = []
    newCols = []
    for index, val in enumerate(cols):
        if (
            val in Constants.COLS_TO_KEEP_FOR_ARCHIVE
            and Constants.COLS_TO_KEEP_FOR_ARCHIVE[val]
        ):
            colIndexes.append(index)
            newCols.append(val)
    archiveRows = [[row[i].strip() for i in colIndexes] for row in rows]
    return newCols, archiveRows


def processRetentionData(
    cols: list, rows: list, listDate: datetime.date
) -> RetentionCounts:
    """Tally good-standing / member / lapsed counts and return them as a
    RetentionCounts dataclass, dated by ``listDate`` (the caller determines
    the list's date — see determineListDate — since it isn't reliably a
    per-row value we should trust blindly).

    Copied from ListManagement/processNewMembers.py processRetentionData,
    minus the CSV-append / Google Drive persistence (the caller persists via
    MembershipSnapshot.update_or_create).
    """
    membersGoodStanding = 0
    membersMember = 0
    membersLapsed = 0
    standingIndex = -1
    for index, val in enumerate(cols):
        if val.strip().lower() == Constants.STANDING_COL:
            standingIndex = index
            break
    if standingIndex == -1:
        raise RetentionCounterException(
            "Couldn't find membership standing column (%r)" % Constants.STANDING_COL
        )

    for row in rows:
        if len(row) != len(cols):
            raise RetentionCounterException(
                "Column/row length mismatch (likely an unescaped comma in the "
                f"source CSV): {list(zip(cols, row))}"
            )
        status = row[standingIndex].strip().lower()
        if status == Constants.MEMBERSHIP_STATUS.GOOD_STANDING:
            membersGoodStanding += 1
        elif status == Constants.MEMBERSHIP_STATUS.MEMBER:
            membersMember += 1
        elif status == Constants.MEMBERSHIP_STATUS.LAPSED:
            membersLapsed += 1
        else:
            raise RetentionCounterException(
                f"Found unexpected membership status: {status!r}"
            )

    return RetentionCounts(
        listDate=listDate,
        goodStanding=membersGoodStanding,
        member=membersMember,
        lapsed=membersLapsed,
        total=membersGoodStanding + membersMember + membersLapsed,
    )


# Date formats seen (or plausible) in national's list_date column / export
# filenames over the years. Tried in order; first match wins.
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d")


def _parseDateAnyFormat(value: str) -> typing.Optional[datetime.date]:
    value = value.strip()
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def extractListDateFromRows(
    cols: list, rows: list
) -> typing.Optional[datetime.date]:
    """Best-effort: read the list's own ``list_date`` column (present on most
    modern exports) off the first row. Returns None if the column is absent,
    empty, or unparseable — callers should fall back to another signal (the
    source zip filename / email date)."""
    try:
        dateIndex = cols.index(Constants.LIST_DATE_COL)
    except ValueError:
        return None
    if not rows:
        return None
    return _parseDateAnyFormat(rows[0][dateIndex])
