import datetime
import os
import tempfile

from django.test import SimpleTestCase

from tools.MembershipList import RetentionCounter


def _writeCsv(path, cols, rows):
    import csv

    with open(path, "w", newline="", encoding="utf8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)


class ReadCsvTests(SimpleTestCase):
    def test_reads_cols_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "list.csv")
            _writeCsv(
                path,
                ["email", "membership_status"],
                [["a@example.com", "member"], ["b@example.com", "lapsed"]],
            )
            cols, rows = RetentionCounter.readCSV(path)
            self.assertEqual(cols, ["email", "membership_status"])
            self.assertEqual(len(rows), 2)


class CheckForNewColsTests(SimpleTestCase):
    def test_all_known_columns_returns_empty(self):
        result = RetentionCounter.checkForNewCols(["email", "membership_status", "zip"])
        self.assertEqual(result.unknownColumns, [])

    def test_unknown_column_is_logged_and_skipped_not_raised(self):
        # Must NOT raise - this is the whole point of the log-and-skip policy
        # (a historical backfill can't abort a list over a renamed column).
        result = RetentionCounter.checkForNewCols(
            ["email", "membership_status", "some_brand_new_national_column"]
        )
        self.assertEqual(result.unknownColumns, ["some_brand_new_national_column"])

    def test_multiple_unknown_columns_all_collected(self):
        result = RetentionCounter.checkForNewCols(
            ["membership_status", "weird_col_1", "weird_col_2"]
        )
        self.assertEqual(set(result.unknownColumns), {"weird_col_1", "weird_col_2"})


class ArchiveAndObfuscateTests(SimpleTestCase):
    def test_drops_pii_columns_keeps_nonpii(self):
        cols = ["first_name", "email", "zip", "membership_status"]
        rows = [["Alice", "a@example.com", "78701", "member"]]
        newCols, archiveRows = RetentionCounter.archiveAndObfuscate(cols, rows)
        self.assertEqual(newCols, ["zip", "membership_status"])
        self.assertEqual(archiveRows, [["78701", "member"]])
        # PII must not survive into the archive rows.
        for row in archiveRows:
            self.assertNotIn("Alice", row)
            self.assertNotIn("a@example.com", row)

    def test_unknown_column_is_excluded_from_archive(self):
        cols = ["membership_status", "some_brand_new_national_column"]
        rows = [["member", "mystery-value"]]
        newCols, archiveRows = RetentionCounter.archiveAndObfuscate(cols, rows)
        self.assertEqual(newCols, ["membership_status"])
        self.assertEqual(archiveRows, [["member"]])


class ProcessRetentionDataTests(SimpleTestCase):
    def test_tallies_counts_correctly(self):
        cols = ["email", "membership_status"]
        rows = [
            ["a@example.com", "member in good standing"],
            ["b@example.com", "member in good standing"],
            ["c@example.com", "member"],
            ["d@example.com", "lapsed"],
            ["e@example.com", "lapsed"],
            ["f@example.com", "lapsed"],
        ]
        counts = RetentionCounter.processRetentionData(
            cols, rows, datetime.date(2024, 3, 1)
        )
        self.assertEqual(counts.listDate, datetime.date(2024, 3, 1))
        self.assertEqual(counts.goodStanding, 2)
        self.assertEqual(counts.member, 1)
        self.assertEqual(counts.lapsed, 3)
        self.assertEqual(counts.total, 6)

    def test_status_is_case_and_whitespace_insensitive(self):
        cols = ["membership_status"]
        rows = [[" Member In Good Standing "], ["LAPSED"]]
        counts = RetentionCounter.processRetentionData(
            cols, rows, datetime.date(2024, 1, 1)
        )
        self.assertEqual(counts.goodStanding, 1)
        self.assertEqual(counts.lapsed, 1)

    def test_missing_standing_column_raises(self):
        with self.assertRaises(RetentionCounter.RetentionCounterException):
            RetentionCounter.processRetentionData(
                ["email"], [["a@example.com"]], datetime.date(2024, 1, 1)
            )

    def test_unexpected_status_value_raises(self):
        with self.assertRaises(RetentionCounter.RetentionCounterException):
            RetentionCounter.processRetentionData(
                ["membership_status"], [["not_a_real_status"]], datetime.date(2024, 1, 1)
            )

    def test_row_column_length_mismatch_raises(self):
        with self.assertRaises(RetentionCounter.RetentionCounterException):
            RetentionCounter.processRetentionData(
                ["email", "membership_status"],
                [["a@example.com"]],  # missing the second column's value
                datetime.date(2024, 1, 1),
            )

    def test_unknown_columns_do_not_block_retention_counting(self):
        # An unrecognized column must not stop us from still tallying
        # retention off membership_status - this is the crux of not losing a
        # whole list's data point over a renamed/added national column.
        cols = ["membership_status", "some_brand_new_national_column"]
        rows = [["member", "x"], ["lapsed", "y"]]
        checkResult = RetentionCounter.checkForNewCols(cols)
        self.assertEqual(checkResult.unknownColumns, ["some_brand_new_national_column"])
        counts = RetentionCounter.processRetentionData(cols, rows, datetime.date(2024, 1, 1))
        self.assertEqual(counts.member, 1)
        self.assertEqual(counts.lapsed, 1)
        self.assertEqual(counts.total, 2)


class ExtractListDateFromRowsTests(SimpleTestCase):
    def test_extracts_iso_date_from_list_date_column(self):
        cols = ["membership_status", "list_date"]
        rows = [["member", "2024-03-01"]]
        self.assertEqual(
            RetentionCounter.extractListDateFromRows(cols, rows), datetime.date(2024, 3, 1)
        )

    def test_extracts_slash_format_date(self):
        cols = ["membership_status", "list_date"]
        rows = [["member", "03/01/2024"]]
        self.assertEqual(
            RetentionCounter.extractListDateFromRows(cols, rows), datetime.date(2024, 3, 1)
        )

    def test_no_list_date_column_returns_none(self):
        cols = ["membership_status"]
        rows = [["member"]]
        self.assertIsNone(RetentionCounter.extractListDateFromRows(cols, rows))

    def test_empty_rows_returns_none(self):
        cols = ["membership_status", "list_date"]
        self.assertIsNone(RetentionCounter.extractListDateFromRows(cols, []))

    def test_unparseable_date_returns_none(self):
        cols = ["membership_status", "list_date"]
        rows = [["member", "not-a-date"]]
        self.assertIsNone(RetentionCounter.extractListDateFromRows(cols, rows))
