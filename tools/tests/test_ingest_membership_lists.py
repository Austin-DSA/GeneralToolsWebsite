"""End-to-end test of `manage.py ingest_membership_lists --from-dir <dir>`
against synthetic zips - this is the offline stand-in for the untestable live
email path (see tools/MembershipList/README.md): a filled-in credential is
the only thing between this code path and the real inbox.
"""

import csv
import datetime
import io
import os
import tempfile
import zipfile

from django.core.management import call_command
from django.test import TestCase

from tools.models import MembershipSnapshot


def _makeMembershipListZip(path, cols, rows):
    csvBuf = io.StringIO()
    writer = csv.writer(csvBuf)
    writer.writerow(cols)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("austin_membership_list.csv", csvBuf.getvalue())


class IngestMembershipListsFromDirTests(TestCase):
    def setUp(self):
        self.tmpDir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpDir.cleanup)
        self.zipsDir = self.tmpDir.name

    def _run(self, expectSystemExit=False, **kwargs):
        out = io.StringIO()
        err = io.StringIO()
        try:
            call_command("ingest_membership_lists", from_dir=self.zipsDir, stdout=out, stderr=err, **kwargs)
        except SystemExit:
            if not expectSystemExit:
                raise
        return out.getvalue(), err.getvalue()

    def test_populates_one_snapshot_per_list_with_correct_counts(self):
        _makeMembershipListZip(
            os.path.join(self.zipsDir, "list-2020-01-01.zip"),
            ["email", "membership_status", "list_date"],
            [
                ["a@example.com", "member in good standing", "2020-01-01"],
                ["b@example.com", "member in good standing", "2020-01-01"],
                ["c@example.com", "member", "2020-01-01"],
                ["d@example.com", "lapsed", "2020-01-01"],
            ],
        )
        _makeMembershipListZip(
            os.path.join(self.zipsDir, "list-2020-02-01.zip"),
            ["email", "membership_status", "list_date"],
            [
                ["a@example.com", "member in good standing", "2020-02-01"],
                ["b@example.com", "lapsed", "2020-02-01"],
            ],
        )

        self._run()

        self.assertEqual(MembershipSnapshot.objects.count(), 2)
        jan = MembershipSnapshot.objects.get(listDate=datetime.date(2020, 1, 1))
        self.assertEqual(jan.goodStanding, 2)
        self.assertEqual(jan.member, 1)
        self.assertEqual(jan.lapsed, 1)
        self.assertEqual(jan.total, 4)
        feb = MembershipSnapshot.objects.get(listDate=datetime.date(2020, 2, 1))
        self.assertEqual(feb.goodStanding, 1)
        self.assertEqual(feb.lapsed, 1)
        self.assertEqual(feb.total, 2)

    def test_falls_back_to_filename_date_when_no_list_date_column(self):
        # No list_date column - the command must recover the date from the
        # "list-YYYY-MM-DD.zip" filename convention instead.
        _makeMembershipListZip(
            os.path.join(self.zipsDir, "list-2021-06-15.zip"),
            ["email", "membership_status"],
            [["a@example.com", "member"]],
        )

        self._run()

        self.assertTrue(MembershipSnapshot.objects.filter(listDate=datetime.date(2021, 6, 15)).exists())

    def test_unknown_column_is_logged_and_skipped_list_still_ingested(self):
        _makeMembershipListZip(
            os.path.join(self.zipsDir, "list-2022-09-01.zip"),
            ["membership_status", "list_date", "some_brand_new_national_column"],
            [["member", "2022-09-01", "mystery-value"], ["lapsed", "2022-09-01", "another-value"]],
        )

        out, err = self._run()

        snapshot = MembershipSnapshot.objects.get(listDate=datetime.date(2022, 9, 1))
        self.assertEqual(snapshot.member, 1)
        self.assertEqual(snapshot.lapsed, 1)
        self.assertIn("some_brand_new_national_column", out)

    def test_rerun_is_idempotent_no_duplicate_rows(self):
        _makeMembershipListZip(
            os.path.join(self.zipsDir, "list-2020-01-01.zip"),
            ["membership_status", "list_date"],
            [["member", "2020-01-01"], ["lapsed", "2020-01-01"]],
        )

        self._run()
        self._run()  # re-run against the same folder - must not double-count

        self.assertEqual(MembershipSnapshot.objects.count(), 1)
        snapshot = MembershipSnapshot.objects.get(listDate=datetime.date(2020, 1, 1))
        self.assertEqual(snapshot.total, 2)

    def test_reingesting_same_list_overwrites_not_duplicates(self):
        path = os.path.join(self.zipsDir, "list-2020-01-01.zip")
        _makeMembershipListZip(
            path, ["membership_status", "list_date"], [["member", "2020-01-01"]]
        )
        self._run()
        self.assertEqual(MembershipSnapshot.objects.get(listDate=datetime.date(2020, 1, 1)).total, 1)

        # Overwrite with a corrected/resent version of the same list showing a
        # different count - update_or_create must replace, not add a row.
        _makeMembershipListZip(
            path,
            ["membership_status", "list_date"],
            [["member", "2020-01-01"], ["lapsed", "2020-01-01"]],
        )
        self._run()

        self.assertEqual(MembershipSnapshot.objects.count(), 1)
        self.assertEqual(MembershipSnapshot.objects.get(listDate=datetime.date(2020, 1, 1)).total, 2)

    def test_dry_run_does_not_write_rows(self):
        _makeMembershipListZip(
            os.path.join(self.zipsDir, "list-2020-01-01.zip"),
            ["membership_status", "list_date"],
            [["member", "2020-01-01"]],
        )

        self._run(dry_run=True)

        self.assertEqual(MembershipSnapshot.objects.count(), 0)

    def test_no_zips_in_dir_is_a_clean_noop(self):
        out, err = self._run()
        self.assertEqual(MembershipSnapshot.objects.count(), 0)
        self.assertIn("Processed: 0", out)

    def test_missing_standing_column_is_reported_as_error_not_a_crash(self):
        _makeMembershipListZip(
            os.path.join(self.zipsDir, "list-2020-01-01.zip"),
            ["email"],  # no membership_status column at all
            [["a@example.com"]],
        )

        out, err = self._run(expectSystemExit=True)

        self.assertEqual(MembershipSnapshot.objects.count(), 0)
        self.assertIn("Errored: 1", out)


class IngestMembershipListsNoCredsTests(TestCase):
    def test_no_from_dir_and_no_creds_configured_warns_and_exits_cleanly(self):
        # devSecrets.py returns None for the membership bot creds in dev, so
        # without --from-dir this must warn and exit 0 - never break the
        # deploy/schedule just because Garrigan hasn't filled in the app
        # password yet.
        out = io.StringIO()
        call_command("ingest_membership_lists", stdout=out)
        self.assertIn("not configured", out.getvalue())
        self.assertEqual(MembershipSnapshot.objects.count(), 0)
