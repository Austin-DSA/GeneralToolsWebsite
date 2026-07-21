# Membership List (ingest + bleeding curve)

Turns national DSA's monthly "Austin Membership List" email into a governed,
PII-stripped retention time series on Echo's own datastore — the **bleeding
curve**: `total` (and the good-standing / member / lapsed split) over
`listDate`, showing the waves of membership loss/gain.

## What it does

1. Fetches every historical membership-list email (5-6 years) from the
   `austindsalistbot` Gmail inbox, or reads a local folder of already-exported
   zips (`--from-dir`).
2. Unzips each list's CSV and strips it down to non-identifying columns only
   (`RetentionCounter.Constants.COLS_TO_KEEP_FOR_ARCHIVE` — the same
   column-obfuscation map as the original `ListManagement` pipeline).
3. Tallies good-standing / member / lapsed / total from the membership-status
   column and stores exactly that aggregate as one `MembershipSnapshot` row,
   keyed by the list's own date (not the ingest date).

Raw PII (names, addresses, emails, phones) is only ever read into memory /
extracted to a temp working dir to compute the counts. It is **never**
persisted to the DB — `MembershipSnapshot` has no PII columns. This is
load-bearing for the initiative's whole PII pitch; do not add one.

## Where this logic came from

Copied (not imported — see `Membership-Engagment-Tools/CLAUDE.md`'s house rule
against cross-folder imports in that repo) from
`Membership-Engagment-Tools/ListManagement/processNewMembers.py` and
`Utils.py`:

- `Constants.COLS_TO_KEEP_FOR_ARCHIVE` → `RetentionCounter.Constants.COLS_TO_KEEP_FOR_ARCHIVE`
- `checkForNewCols` → `RetentionCounter.checkForNewCols` (behavior changed, see below)
- `archiveAndObfuscate` → `RetentionCounter.archiveAndObfuscate`
- `processRetentionData` → `RetentionCounter.processRetentionData` (now returns
  a `RetentionCounts` dataclass instead of appending to a local CSV)
- `Utils.readCSV` → `RetentionCounter.readCSV`

## Decision: unknown columns are logged and skipped, not fatal

The original `checkForNewCols` **raised** on any column not in
`COLS_TO_KEEP_FOR_ARCHIVE`, aborting the whole list. That's fine for a weekly
single-list pipeline where a human notices and updates the map. It is *not*
fine for a historical backfill over 5-6 years of lists, where national has
renamed/added columns more than once — one unrecognized column would silently
delete that month's data point from the bleeding curve.

Policy implemented here: `checkForNewCols` logs a warning per unrecognized
column and returns them in a `ColumnCheckResult.unknownColumns` list instead of
raising. Unrecognized columns are simply excluded from the obfuscated archive
(`archiveAndObfuscate` only keeps columns explicitly mapped `True` — an
unknown column was never going to be kept anyway) and retention counting is
unaffected (it only needs the `membership_status` column, which every list has
had). `ingest_membership_lists` accumulates the unknown-column set across the
whole run and prints a one-line summary at the end, so a human (Garrigan,
running the real backfill) can see at a glance whether national added a column
worth mapping — without any list's data being lost over it.

## Determining a list's date

`processRetentionData` needs a `listDate` to date the resulting snapshot. It
does **not** default to "today" (unlike the original script's
`Utils.Constants.TODAY_STR`) — that would be actively wrong for a backfill
where every list is processed on the same day. `ingest_membership_lists`
resolves it in this order, per zip:

1. The list's own `list_date` column, if present and parseable
   (`RetentionCounter.extractListDateFromRows`).
2. A `YYYY-MM-DD` date embedded in the zip's filename (the live email fetch
   names files `list-YYYY-MM-DD.zip` after the message date; a `--from-dir`
   export may or may not follow that convention).
3. The source email's `Date` header (only available on the live-email path).

If none of the three yield a date, that zip is skipped with a logged error
rather than guessed at.

## Testing

Fully testable without inbox access — see `tools/tests/test_retention_counter.py`
(synthetic CSVs, including the unknown-column case) and
`tools/tests/test_email_api_bulk_fetch.py` (a fake IMAP object standing in for
Gmail). The command-level end-to-end path is exercised via
`--from-dir <folder-of-synthetic-zips>` (see `tools/tests/test_ingest_membership_lists.py`).
