"""Unit tests for EmailApi.EmailAccount.downloadAllZipAttachmentsFrom against a
fake IMAP object - there's no way to test the real austindsalistbot inbox in
this build (see tools/MembershipList/README.md), so the bulk fetch is proven
against a stand-in imaplib.IMAP4_SSL.
"""

import datetime
import io
import os
import tempfile
import zipfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from unittest import mock

from django.test import SimpleTestCase

from tools.EmailApi.EmailApi import EmailAccount, EmailApiException


def _makeZipBytes(memberName: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(memberName, content)
    return buf.getvalue()


def _makeEmailMessage(dateStr: str, attachmentName: str, attachmentBytes: bytes) -> bytes:
    msg = MIMEMultipart()
    msg["Subject"] = "Austin Membership List"
    msg["From"] = "no-reply@actionkit.com"
    msg["To"] = "austindsalistbot@example.com"
    msg["Date"] = dateStr
    part = MIMEApplication(attachmentBytes, _subtype="zip")
    part.add_header("Content-Disposition", "attachment", filename=attachmentName)
    msg.attach(part)
    return msg.as_bytes()


class _FakeImap:
    """Minimal stand-in for imaplib.IMAP4_SSL covering just what EmailAccount
    and _getAllEmailsFrom touch."""

    def __init__(self, messagesById):
        # messagesById: {b"1": raw_rfc822_bytes, ...}
        self.messagesById = messagesById
        self.stored = []

    def login(self, username, password):
        return "OK", [b"Logged in"]

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        # Our fake ignores the actual search criteria and returns every
        # message - the criteria construction itself is exercised implicitly
        # (a malformed call would raise before reaching here in real imaplib,
        # but this fake's job is to prove downloadAllZipAttachmentsFrom's
        # download/parse/return behavior, not IMAP query syntax).
        ids = b" ".join(self.messagesById.keys())
        return "OK", [ids]

    def fetch(self, msgId, what):
        return "OK", [(msgId, self.messagesById[msgId])]

    def store(self, msgId, flag, value):
        self.stored.append((msgId, flag, value))
        return "OK", [b""]

    def logout(self):
        return "OK", [b"Logged out"]


class DownloadAllZipAttachmentsFromTests(SimpleTestCase):
    def _buildAccount(self, messagesById):
        fakeImap = _FakeImap(messagesById)
        with mock.patch("tools.EmailApi.EmailApi.imaplib.IMAP4_SSL", return_value=fakeImap):
            account = EmailAccount("bot@example.com", "app-password")
        return account, fakeImap

    def test_downloads_every_matching_email_not_just_newest(self):
        zip1 = _makeZipBytes("austin_membership_list.csv", b"col1,col2\na,b\n")
        zip2 = _makeZipBytes("austin_membership_list.csv", b"col1,col2\nc,d\n")
        messages = {
            b"1": _makeEmailMessage("Mon, 01 Jan 2024 10:00:00 -0600", "austin_membership_list.zip", zip1),
            b"2": _makeEmailMessage("Fri, 01 Mar 2024 10:00:00 -0600", "austin_membership_list.zip", zip2),
        }
        account, fakeImap = self._buildAccount(messages)

        with tempfile.TemporaryDirectory() as downloadDir:
            results = account.downloadAllZipAttachmentsFrom(
                fromAddress="no-reply@actionkit.com",
                subjectContaining="Austin Membership List",
                downloadDir=downloadDir,
                expectedFileName="austin_membership_list.zip",
            )

            self.assertEqual(len(results), 2)
            # Sorted ascending by date.
            self.assertEqual(results[0][0].date(), datetime.date(2024, 1, 1))
            self.assertEqual(results[1][0].date(), datetime.date(2024, 3, 1))
            for _, path in results:
                self.assertTrue(os.path.exists(path))
                self.assertTrue(zipfile.is_zipfile(path))

    def test_does_not_mark_messages_as_read(self):
        # Idempotent re-runs / not disturbing the inbox is load-bearing - the
        # bulk fetch must never flip \Seen, unlike the single-newest-unread
        # download path.
        zipBytes = _makeZipBytes("austin_membership_list.csv", b"col1\na\n")
        messages = {
            b"1": _makeEmailMessage("Mon, 01 Jan 2024 10:00:00 -0600", "austin_membership_list.zip", zipBytes),
        }
        account, fakeImap = self._buildAccount(messages)

        with tempfile.TemporaryDirectory() as downloadDir:
            account.downloadAllZipAttachmentsFrom(
                fromAddress="no-reply@actionkit.com",
                subjectContaining="Austin Membership List",
                downloadDir=downloadDir,
            )

        self.assertEqual(fakeImap.stored, [])

    def test_saved_filename_is_dated_by_message_date(self):
        zipBytes = _makeZipBytes("austin_membership_list.csv", b"col1\na\n")
        messages = {
            b"1": _makeEmailMessage("Wed, 15 May 2019 09:30:00 -0600", "austin_membership_list.zip", zipBytes),
        }
        account, _ = self._buildAccount(messages)

        with tempfile.TemporaryDirectory() as downloadDir:
            results = account.downloadAllZipAttachmentsFrom(
                fromAddress="no-reply@actionkit.com",
                subjectContaining="Austin Membership List",
                downloadDir=downloadDir,
            )
            self.assertEqual(os.path.basename(results[0][1]), "list-2019-05-15.zip")

    def test_no_matching_emails_returns_empty_list(self):
        account, _ = self._buildAccount({})
        with tempfile.TemporaryDirectory() as downloadDir:
            results = account.downloadAllZipAttachmentsFrom(
                fromAddress="no-reply@actionkit.com",
                subjectContaining="Austin Membership List",
                downloadDir=downloadDir,
            )
            self.assertEqual(results, [])

    def test_wrong_attachment_filename_raises(self):
        zipBytes = _makeZipBytes("austin_membership_list.csv", b"col1\na\n")
        messages = {
            b"1": _makeEmailMessage("Mon, 01 Jan 2024 10:00:00 -0600", "unexpected_name.zip", zipBytes),
        }
        account, _ = self._buildAccount(messages)
        with tempfile.TemporaryDirectory() as downloadDir:
            with self.assertRaises(EmailApiException):
                account.downloadAllZipAttachmentsFrom(
                    fromAddress="no-reply@actionkit.com",
                    subjectContaining="Austin Membership List",
                    downloadDir=downloadDir,
                    expectedFileName="austin_membership_list.zip",
                )
