"""Tests for the resolution submission + signature sign-on feature.

The AN MIG check is always exercised through a validator (the mock by default;
a fake client for the live-gate test), so nothing here touches the network.
"""
import datetime
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from tools.models import Resolution, ResolutionSignature, PostedEvents
from tools.resolutionText import normalizedTextHash, renderMarkdown
from tools.ActionNetworkAPI import ActionNetworkAPI
from tools.ActionNetworkAPI.migValidator import (
    MIGStatus, MockANMIGValidator, LiveANMIGValidator,
)
from tools.tests.support.factories import UserFactory


def _futureMeeting(daysAhead=40, title="July 2026 GBM"):
    now = datetime.datetime.now(datetime.UTC)
    start = now + datetime.timedelta(days=daysAhead)
    return PostedEvents.objects.create(
        title=title, start=start, end=start + datetime.timedelta(hours=2),
        timezone="America/Chicago", locationName="", streetAddress="", city="",
        state="", zip="", country="", description="", instructions="",
        dateCreated=now, datePublished=now, anManageLink="", anShareLink="",
        gCalLink="", zoomLink="", zoomAccount="", reason="",
    )


# --- safe markdown renderer ------------------------------------------------

class MarkdownRendererTests(TestCase):
    def test_basic_formatting(self):
        html = renderMarkdown("# Title\n\nSome **bold** and _italic_ text.\n\n- one\n- two")
        self.assertIn("<h2>Title</h2>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)
        self.assertIn("<li>one</li>", html)

    def test_escapes_raw_html_xss(self):
        html = renderMarkdown("<script>alert('x')</script>\n\nhi")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_drops_unsafe_link_scheme(self):
        html = renderMarkdown("[click](javascript:alert(1))")
        self.assertNotIn("javascript:", html)
        self.assertIn("click", html)

    def test_keeps_safe_link(self):
        html = renderMarkdown("[BRT](https://example.org/brt)")
        self.assertIn('href="https://example.org/brt"', html)

    def test_empty(self):
        self.assertEqual(renderMarkdown(""), "")


class NormalizedHashTests(TestCase):
    def test_crlf_and_whitespace_are_noops(self):
        a = normalizedTextHash("Whereas this\nResolved that\n")
        b = normalizedTextHash("  Whereas this\r\nResolved that  ")
        self.assertEqual(a, b)

    def test_real_change_differs(self):
        self.assertNotEqual(normalizedTextHash("one"), normalizedTextHash("two"))


# --- model -----------------------------------------------------------------

class ResolutionModelTests(TestCase):
    def setUp(self):
        self.member = UserFactory.make("proponent")

    def _make(self, kind=Resolution.Kind.PROJECT_COMMITTEE, text="Body"):
        return Resolution.objects.create(
            title="T", kind=kind, text=text, proponent=self.member,
        )

    def test_threshold_and_lead_days_by_kind(self):
        self.assertIsNone(self._make(Resolution.Kind.GENERAL).threshold)
        self.assertEqual(self._make(Resolution.Kind.PROJECT_COMMITTEE).threshold, 25)
        self.assertEqual(self._make(Resolution.Kind.BYLAWS_AMENDMENT).threshold, 35)
        self.assertEqual(self._make(Resolution.Kind.GENERAL).leadDays, 0)
        self.assertEqual(self._make(Resolution.Kind.PROJECT_COMMITTEE).leadDays, 10)
        self.assertEqual(self._make(Resolution.Kind.BYLAWS_AMENDMENT).leadDays, 21)

    def test_general_meets_threshold_with_no_signatures(self):
        self.assertTrue(self._make(Resolution.Kind.GENERAL).meetsThreshold)

    def test_only_verified_signatures_count(self):
        res = self._make()
        signer1 = UserFactory.make("s1")
        signer2 = UserFactory.make("s2")
        ResolutionSignature.objects.create(
            resolution=res, member=signer1, textHashAtSigning="h",
            verified=True, verificationStatus=MIGStatus.OK,
            checkedAt=datetime.datetime.now(datetime.UTC),
        )
        ResolutionSignature.objects.create(
            resolution=res, member=signer2, textHashAtSigning="h",
            verified=False, verificationStatus=MIGStatus.EXPIRED,
            checkedAt=datetime.datetime.now(datetime.UTC),
        )
        self.assertEqual(res.signatureCount, 1)

    def test_lock_text_is_idempotent(self):
        res = self._make(text="Final text")
        res.lockText()
        self.assertTrue(res.locked)
        self.assertEqual(res.lockedTextHash, normalizedTextHash("Final text"))
        firstLockedAt = res.lockedAt
        res.lockText()
        self.assertEqual(res.lockedAt, firstLockedAt)

    def test_replace_text_resets_signatures_on_real_change(self):
        res = self._make(text="Original")
        signer = UserFactory.make("s1")
        ResolutionSignature.objects.create(
            resolution=res, member=signer, textHashAtSigning="h",
            verified=True, verificationStatus=MIGStatus.OK,
            checkedAt=datetime.datetime.now(datetime.UTC),
        )
        res.lockText()
        didReset = res.replaceText("Completely different text")
        self.assertTrue(didReset)
        self.assertFalse(res.locked)
        self.assertEqual(res.signatures.count(), 0)

    def test_replace_text_noop_resave_preserves_signatures(self):
        res = self._make(text="Original body")
        signer = UserFactory.make("s1")
        ResolutionSignature.objects.create(
            resolution=res, member=signer, textHashAtSigning="h",
            verified=True, verificationStatus=MIGStatus.OK,
            checkedAt=datetime.datetime.now(datetime.UTC),
        )
        res.lockText()
        # Same text but Windows line endings + trailing whitespace.
        didReset = res.replaceText("  Original body\r\n")
        self.assertFalse(didReset)
        self.assertTrue(res.locked)
        self.assertEqual(res.signatures.count(), 1)

    def test_deadline_null_guard(self):
        res = self._make()  # no targetMeeting
        self.assertIsNone(res.deadline())
        self.assertIsNone(res.deadlineMet())

    def test_deadline_computed_from_meeting(self):
        meeting = _futureMeeting(daysAhead=40)
        res = Resolution.objects.create(
            title="T", kind=Resolution.Kind.BYLAWS_AMENDMENT, text="x",
            proponent=self.member, targetMeeting=meeting,
        )
        expected = meeting.start - datetime.timedelta(days=21)
        self.assertEqual(res.deadline(), expected)
        self.assertTrue(res.deadlineMet())  # filed now, 40 days out, 21-day lead


# --- validators ------------------------------------------------------------

class _FakeClient:
    """Stands in for ActionNetworkAPI; returns a canned (status, person)."""
    def __init__(self, status, person):
        self._status = status
        self._person = person

    def getPersonForVoteValidation(self, email):
        return (self._status, self._person)


def _person(memberStatus=True, chapter="austin", expired=False):
    today = datetime.date.today()
    return ActionNetworkAPI.PersonInfoForVoteValidation(
        chapter=chapter, memberStatus=memberStatus,
        expireDate=today - datetime.timedelta(days=1) if expired else today + datetime.timedelta(days=300),
        joinDate=today - datetime.timedelta(days=400),
    )


class LiveValidatorGateTests(TestCase):
    def _verify(self, status, person):
        validator = LiveANMIGValidator("tok")
        validator._client = _FakeClient(status, person)  # bypass network init
        return validator.verify("a@b.org")

    def test_mig_passes(self):
        result = self._verify(ActionNetworkAPI.GetPersonAPIReturnStatus.SUCCESS, _person(True))
        self.assertTrue(result.ok)
        self.assertEqual(result.status, MIGStatus.OK)

    def test_wrong_chapter(self):
        result = self._verify(ActionNetworkAPI.GetPersonAPIReturnStatus.SUCCESS, _person(False, chapter="dallas"))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, MIGStatus.WRONG_CHAPTER)

    def test_expired(self):
        result = self._verify(ActionNetworkAPI.GetPersonAPIReturnStatus.SUCCESS, _person(False, expired=True))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, MIGStatus.EXPIRED)

    def test_not_found(self):
        result = self._verify(ActionNetworkAPI.GetPersonAPIReturnStatus.NOT_FOUND, None)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, MIGStatus.NOT_FOUND)

    def test_client_exception_fails_closed(self):
        validator = LiveANMIGValidator("tok")

        class Boom:
            def getPersonForVoteValidation(self, email):
                raise RuntimeError("network down")
        validator._client = Boom()
        result = validator.verify("a@b.org")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, MIGStatus.API_ERROR)


# --- views -----------------------------------------------------------------

class _ViewBase(TestCase):
    def setUp(self):
        self.member = UserFactory.make("member")
        self.secretary = UserFactory.make("sec", perms=("administerResolutions",))

    def _make(self, kind=Resolution.Kind.PROJECT_COMMITTEE, text="Body text", proponent=None):
        return Resolution.objects.create(
            title="A Resolution", kind=kind, text=text,
            proponent=proponent or self.member,
        )


class AuthGateTests(_ViewBase):
    def test_anonymous_redirected(self):
        res = self._make()
        for url in [
            reverse("submit-resolution"),
            reverse("sign-resolution"),
            reverse("resolution-status"),
            reverse("resolution-detail", kwargs={"pk": res.pk}),
            reverse("resolution-edit", kwargs={"pk": res.pk}),
        ]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn("/accounts/login", resp.url, url)


class SubmitTests(_ViewBase):
    def test_submit_persists_and_redirects(self):
        self.client.force_login(self.member)
        resp = self.client.post(reverse("submit-resolution"), {
            "title": "Endorse the BRT plan",
            "kind": Resolution.Kind.GENERAL,
            "text": "Whereas...\n\nResolved...",
        })
        self.assertEqual(resp.status_code, 302)
        res = Resolution.objects.get(title="Endorse the BRT plan")
        self.assertEqual(res.proponent, self.member)
        self.assertEqual(res.kind, Resolution.Kind.GENERAL)
        self.assertEqual(resp.url, reverse("resolution-detail", kwargs={"pk": res.pk}))

    def test_submit_invalid_kind_rerenders(self):
        self.client.force_login(self.member)
        resp = self.client.post(reverse("submit-resolution"), {
            "title": "X", "kind": "NONSENSE", "text": "body",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Resolution.objects.filter(title="X").exists())


class SignOnTests(_ViewBase):
    def _signWith(self, validator, res, user):
        with mock.patch("tools.resolutionViews.getMIGValidator", return_value=validator):
            return self.client.post(reverse("resolution-detail", kwargs={"pk": res.pk}), {"affirm": "on"})

    def test_sign_on_happy_path_records_and_locks(self):
        res = self._make(text="Lock me")
        signer = UserFactory.make("signer1")
        self.client.force_login(signer)
        resp = self._signWith(MockANMIGValidator(), res, signer)
        self.assertEqual(resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.signatureCount, 1)
        self.assertTrue(res.locked)
        sig = ResolutionSignature.objects.get(resolution=res, member=signer)
        self.assertTrue(sig.verified)
        self.assertEqual(sig.verificationStatus, MIGStatus.OK)
        self.assertEqual(sig.textHashAtSigning, normalizedTextHash("Lock me"))

    def test_failed_check_does_not_record(self):
        res = self._make()
        signer = UserFactory.make("signer2")
        self.client.force_login(signer)
        validator = MockANMIGValidator(defaultStatus=MIGStatus.EXPIRED)
        resp = self._signWith(validator, res, signer)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ResolutionSignature.objects.filter(resolution=res, member=signer).exists())
        res.refresh_from_db()
        self.assertFalse(res.locked)

    def test_api_error_fails_closed(self):
        res = self._make()
        signer = UserFactory.make("signer3")
        self.client.force_login(signer)
        validator = MockANMIGValidator(defaultStatus=MIGStatus.API_ERROR)
        self._signWith(validator, res, signer)
        self.assertFalse(ResolutionSignature.objects.filter(resolution=res, member=signer).exists())

    def test_duplicate_sign_on_blocked(self):
        res = self._make()
        signer = UserFactory.make("signer4")
        self.client.force_login(signer)
        self._signWith(MockANMIGValidator(), res, signer)
        self._signWith(MockANMIGValidator(), res, signer)
        self.assertEqual(
            ResolutionSignature.objects.filter(resolution=res, member=signer).count(), 1
        )

    def test_sign_on_refused_without_account_email(self):
        # No email means the MIG check cannot run; we refuse rather than let an
        # empty address slip past the (permissive) mock validator.
        res = self._make()
        signer = UserFactory.make("noemail")
        signer.email = ""
        signer.save()
        self.client.force_login(signer)
        resp = self._signWith(MockANMIGValidator(), res, signer)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "no email on file")
        self.assertFalse(
            ResolutionSignature.objects.filter(resolution=res, member=signer).exists()
        )


class DetailRenderSafetyTests(_ViewBase):
    def test_detail_view_renders_stored_markdown_safely(self):
        # The renderer is unit-tested; this pins the protection end-to-end through
        # the mark_safe(renderMarkdown(...)) the detail view does.
        res = self._make(text="<script>alert('xss')</script>\n\n[c](javascript:alert(1))")
        self.client.force_login(self.member)
        resp = self.client.get(reverse("resolution-detail", kwargs={"pk": res.pk}))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("<script>alert", body)       # raw injection never reaches the page
        self.assertIn("&lt;script&gt;", body)          # rendered as escaped text instead
        self.assertNotIn('href="javascript:', body)    # unsafe scheme dropped from links


class EditResetTests(_ViewBase):
    def test_edit_after_lock_requires_confirm_then_resets(self):
        res = self._make(text="Original")
        signer = UserFactory.make("signer5")
        ResolutionSignature.objects.create(
            resolution=res, member=signer, textHashAtSigning=normalizedTextHash("Original"),
            verified=True, verificationStatus=MIGStatus.OK,
            checkedAt=datetime.datetime.now(datetime.UTC),
        )
        res.lockText()
        self.client.force_login(self.member)  # the proponent
        url = reverse("resolution-edit", kwargs={"pk": res.pk})

        # Without confirm: re-renders, no change applied.
        resp = self.client.post(url, {"text": "New wording entirely"})
        self.assertEqual(resp.status_code, 200)
        res.refresh_from_db()
        self.assertEqual(res.signatureCount, 1)
        self.assertTrue(res.locked)

        # With confirm: applies + resets sign-ons.
        resp = self.client.post(url, {"text": "New wording entirely", "confirmReset": "on"})
        self.assertEqual(resp.status_code, 302)
        res.refresh_from_db()
        self.assertEqual(res.signatureCount, 0)
        self.assertFalse(res.locked)

    def test_non_proponent_cannot_edit(self):
        res = self._make(proponent=self.member)
        other = UserFactory.make("interloper")
        self.client.force_login(other)
        resp = self.client.get(reverse("resolution-edit", kwargs={"pk": res.pk}))
        self.assertEqual(resp.status_code, 403)

    def test_scheduled_resolution_is_read_only(self):
        # Once scheduled (or adopted) the text is frozen; even the proponent gets
        # a 403, on both the form GET and a sneaky POST.
        res = self._make(proponent=self.member)
        res.status = Resolution.Status.SCHEDULED
        res.save()
        self.client.force_login(self.member)
        url = reverse("resolution-edit", kwargs={"pk": res.pk})
        self.assertEqual(self.client.get(url).status_code, 403)
        resp = self.client.post(url, {"text": "Sneaky change"})
        self.assertEqual(resp.status_code, 403)
        res.refresh_from_db()
        self.assertEqual(res.text, "Body text")  # unchanged

    def test_secretary_can_edit_another_members_gathering_resolution(self):
        # The Secretary (administerResolutions) edits via the proponent-OR-perm
        # branch, not because they own the resolution.
        res = self._make(proponent=self.member)
        self.client.force_login(self.secretary)
        url = reverse("resolution-edit", kwargs={"pk": res.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        resp = self.client.post(url, {"text": "Secretary cleanup"})
        self.assertEqual(resp.status_code, 302)
        res.refresh_from_db()
        self.assertEqual(res.text, "Secretary cleanup")


class DashboardTests(_ViewBase):
    def test_dashboard_requires_permission(self):
        self._make()
        self.client.force_login(self.member)  # no administerResolutions
        resp = self.client.get(reverse("resolution-status"))
        self.assertEqual(resp.status_code, 302)  # permission_required redirects

    def test_on_deck_shows_in_flight_only(self):
        gathering = self._make(kind=Resolution.Kind.GENERAL)  # title "A Resolution"
        Resolution.objects.create(
            title="Already Adopted", kind=Resolution.Kind.GENERAL, text="x",
            proponent=self.member, status=Resolution.Status.ADOPTED)
        self.client.force_login(self.secretary)
        resp = self.client.get(reverse("resolution-status"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "On Deck")
        self.assertContains(resp, "no threshold")        # general shows no threshold
        self.assertContains(resp, gathering.title)
        self.assertNotContains(resp, "Already Adopted")  # adopted is not in flight


# --- lifecycle state machine -----------------------------------------------

class VoteThresholdTests(TestCase):
    def setUp(self):
        self.member = UserFactory.make("vt-prop")

    def _make(self, kind):
        return Resolution.objects.create(title="V", kind=kind, text="x", proponent=self.member)

    def test_majority_kinds(self):
        for kind in (Resolution.Kind.GENERAL, Resolution.Kind.PROJECT_COMMITTEE):
            r = self._make(kind)
            self.assertTrue(r.votePasses(6, 5))
            self.assertFalse(r.votePasses(5, 5))   # a tie is not a majority
            self.assertFalse(r.votePasses(0, 0))   # no votes cast

    def test_two_thirds_kinds(self):
        for kind in (Resolution.Kind.BYLAWS_AMENDMENT, Resolution.Kind.CANDIDATE_ENDORSEMENT):
            r = self._make(kind)
            self.assertTrue(r.votePasses(2, 1))     # exactly two-thirds passes
            self.assertFalse(r.votePasses(2, 2))    # one-half fails
            self.assertFalse(r.votePasses(65, 35))  # 65% < 66.7%
            self.assertTrue(r.votePasses(67, 33))


class TransitionTests(TestCase):
    def setUp(self):
        self.member = UserFactory.make("tr-prop")
        self.actor = UserFactory.make("tr-sec", perms=("administerResolutions",))
        self.meeting = _futureMeeting()

    def _make(self, kind=Resolution.Kind.PROJECT_COMMITTEE, status=Resolution.Status.GATHERING):
        return Resolution.objects.create(
            title="T", kind=kind, text="Body", proponent=self.member, status=status)

    def test_schedule_moves_to_scheduled_and_logs_event(self):
        r = self._make()
        r.schedule(meeting=self.meeting, actor=self.actor)
        self.assertEqual(r.status, Resolution.Status.SCHEDULED)
        self.assertEqual(r.targetMeeting, self.meeting)
        self.assertEqual(r.events.count(), 1)
        event = r.events.first()
        self.assertEqual(event.fromStatus, Resolution.Status.GATHERING)
        self.assertEqual(event.toStatus, Resolution.Status.SCHEDULED)
        self.assertEqual(event.actor, self.actor)

    def test_record_vote_adopts_on_majority(self):
        r = self._make(status=Resolution.Status.SCHEDULED)
        r.recordVote(10, 2, 1, actor=self.actor)
        self.assertEqual(r.status, Resolution.Status.ADOPTED)
        self.assertIsNotNone(r.decidedAt)
        # effectiveDate is the chapter-local decision date, not the raw UTC date
        # (see ChapterLocalDateTests for the boundary case this guards).
        self.assertEqual(r.effectiveDate, r.decidedDateLocal())
        self.assertTrue(r.slug)
        self.assertTrue(r.locked)        # adopted text is frozen
        self.assertTrue(r.isInEffect)

    def test_record_vote_rejects_when_failing(self):
        r = self._make(status=Resolution.Status.SCHEDULED)
        r.recordVote(3, 10, 0, actor=self.actor)
        self.assertEqual(r.status, Resolution.Status.REJECTED)
        self.assertFalse(r.isInEffect)

    def test_amendment_needs_two_thirds(self):
        r = self._make(kind=Resolution.Kind.BYLAWS_AMENDMENT, status=Resolution.Status.SCHEDULED)
        r.recordVote(6, 5, 0, actor=self.actor)  # 54% would pass a majority, not 2/3
        self.assertEqual(r.status, Resolution.Status.REJECTED)

    def test_withdraw(self):
        r = self._make()
        r.withdraw(actor=self.actor, note="duplicate")
        self.assertEqual(r.status, Resolution.Status.WITHDRAWN)
        self.assertEqual(r.events.first().note, "duplicate")

    def test_supersede_takes_it_out_of_effect(self):
        adopted = self._make(status=Resolution.Status.ADOPTED)
        newer = self._make(status=Resolution.Status.ADOPTED)
        adopted.supersede(actor=self.actor, replacement=newer)
        self.assertEqual(adopted.status, Resolution.Status.SUPERSEDED)
        self.assertEqual(adopted.supersededBy, newer)
        self.assertFalse(adopted.isInEffect)
        self.assertTrue(newer.isInEffect)

    def test_send_back_reopens_gathering(self):
        r = self._make(status=Resolution.Status.SCHEDULED)
        r.sendBackToGathering(actor=self.actor)
        self.assertEqual(r.status, Resolution.Status.GATHERING)

    def test_illegal_transitions_raise(self):
        adopted = self._make(status=Resolution.Status.ADOPTED)
        with self.assertRaises(ValueError):
            adopted.schedule(meeting=self.meeting, actor=self.actor)
        with self.assertRaises(ValueError):
            adopted.withdraw(actor=self.actor)
        gathering = self._make()
        with self.assertRaises(ValueError):
            gathering.supersede(actor=self.actor)


class LifecycleViewTests(_ViewBase):
    def setUp(self):
        super().setUp()
        self.meeting = _futureMeeting()

    def test_schedule_view_requires_permission(self):
        r = self._make()
        self.client.force_login(self.member)  # no administerResolutions
        resp = self.client.post(
            reverse("resolution-schedule", kwargs={"pk": r.pk}),
            {"targetMeeting": self.meeting.pk})
        self.assertEqual(resp.status_code, 302)
        r.refresh_from_db()
        self.assertEqual(r.status, Resolution.Status.GATHERING)

    def test_schedule_view_happy_path(self):
        r = self._make()
        self.client.force_login(self.secretary)
        resp = self.client.post(
            reverse("resolution-schedule", kwargs={"pk": r.pk}),
            {"targetMeeting": self.meeting.pk})
        self.assertEqual(resp.status_code, 302)
        r.refresh_from_db()
        self.assertEqual(r.status, Resolution.Status.SCHEDULED)

    def test_record_vote_view_adopts(self):
        r = self._make(kind=Resolution.Kind.GENERAL)
        r.status = Resolution.Status.SCHEDULED
        r.save()
        self.client.force_login(self.secretary)
        resp = self.client.post(
            reverse("resolution-record-vote", kwargs={"pk": r.pk}),
            {"votesYes": "20", "votesNo": "3", "votesAbstain": "1"})
        self.assertEqual(resp.status_code, 302)
        r.refresh_from_db()
        self.assertEqual(r.status, Resolution.Status.ADOPTED)
        self.assertEqual(r.votesYes, 20)

    def test_action_views_are_post_only(self):
        r = self._make()
        self.client.force_login(self.secretary)
        resp = self.client.get(reverse("resolution-withdraw", kwargs={"pk": r.pk}))
        self.assertEqual(resp.status_code, 405)

    def test_export_returns_repo_markdown(self):
        r = self._make(kind=Resolution.Kind.GENERAL)
        r.status = Resolution.Status.SCHEDULED
        r.save()
        r.recordVote(50, 1, 0, actor=self.secretary)
        self.client.force_login(self.secretary)
        resp = self.client.get(reverse("resolution-export", kwargs={"pk": r.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/markdown", resp["Content-Type"])
        body = resp.content.decode()
        self.assertIn("# A Resolution", body)
        self.assertIn("50 Yes - 1 No - 0 Abstain", body)

    def test_all_lifecycle_actions_require_permission(self):
        # Each secretary route carries its own @permission_required; a dropped
        # decorator on any one must fail CI. A plain member is redirected and
        # nothing mutates.
        r = self._make()
        r.status = Resolution.Status.SCHEDULED
        r.save()
        self.client.force_login(self.member)  # no administerResolutions
        posts = {
            "resolution-schedule": {"targetMeeting": self.meeting.pk},
            "resolution-send-back": {},
            "resolution-record-vote": {"votesYes": "5", "votesNo": "0", "votesAbstain": "0"},
            "resolution-withdraw": {},
            "resolution-supersede": {},
        }
        for name, data in posts.items():
            resp = self.client.post(reverse(name, kwargs={"pk": r.pk}), data)
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn("/accounts/login", resp.url, name)
        resp = self.client.get(reverse("resolution-export", kwargs={"pk": r.pk}))  # GET route
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login", resp.url)
        r.refresh_from_db()
        self.assertEqual(r.status, Resolution.Status.SCHEDULED)  # untouched


class ChapterLocalDateTests(TestCase):
    def test_decision_date_and_deadline_are_chapter_local_not_utc(self):
        # 02:30 UTC on Jul 2 is 21:30 the prior evening in Central. The decision
        # date must read Jul 1, and the deadline must label a Central zone - the
        # bug was rendering both in UTC (a day off, mislabeled).
        member = UserFactory.make("tz-prop")
        meeting = _futureMeeting()
        res = Resolution.objects.create(
            title="TZ", kind=Resolution.Kind.PROJECT_COMMITTEE, text="x",
            proponent=member, targetMeeting=meeting,
            decidedAt=datetime.datetime(2026, 7, 2, 2, 30, tzinfo=datetime.UTC),
        )
        self.assertEqual(res.decidedDateLocal(), datetime.date(2026, 7, 1))
        deadlineStr = res.getDeadlineStr()
        self.assertNotIn("UTC", deadlineStr)
        self.assertTrue("CDT" in deadlineStr or "CST" in deadlineStr, deadlineStr)


class PublicListTests(_ViewBase):
    def _adopt(self, title, kind=Resolution.Kind.GENERAL, yes=50, no=1):
        r = Resolution.objects.create(
            title=title, kind=kind, text="x", proponent=self.member,
            status=Resolution.Status.SCHEDULED)
        r.recordVote(yes, no, 0, actor=self.secretary)
        return r

    def test_in_effect_lists_adopted_not_superseded(self):
        adopted = self._adopt("Adopted And Governing")
        old = self._adopt("Old Superseded One")
        old.supersede(actor=self.secretary, replacement=adopted)
        Resolution.objects.create(
            title="Still Gathering Petition", kind=Resolution.Kind.GENERAL,
            text="x", proponent=self.member, status=Resolution.Status.GATHERING)
        self.client.force_login(self.member)
        resp = self.client.get(reverse("resolutions-in-effect"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Adopted And Governing")
        self.assertNotContains(resp, "Old Superseded One")
        self.assertNotContains(resp, "Still Gathering Petition")

    def test_archive_filters_and_paginates(self):
        for i in range(12):
            Resolution.objects.create(
                title=f"Gathering Item {i}", kind=Resolution.Kind.GENERAL,
                text="x", proponent=self.member)
        self._adopt("An Adopted Item")  # 13 total
        self.client.force_login(self.member)

        resp = self.client.get(reverse("resolutions-archive"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Showing 1 to 10 of 13")

        resp = self.client.get(reverse("resolutions-archive"), {"status": Resolution.Status.ADOPTED})
        self.assertContains(resp, "An Adopted Item")
        self.assertNotContains(resp, "Gathering Item 0")

    def test_late_sign_on_blocked_by_deadline(self):
        # Meeting tomorrow; a project committee needs a 10-day filing lead, so
        # the deadline is already in the past and sign-ons must be refused.
        soon = _futureMeeting(daysAhead=1, title="Tomorrow GBM")
        r = Resolution.objects.create(
            title="Filed Too Late", kind=Resolution.Kind.PROJECT_COMMITTEE,
            text="x", proponent=self.member, targetMeeting=soon)
        signer = UserFactory.make("late-signer")
        self.client.force_login(signer)
        with mock.patch("tools.resolutionViews.getMIGValidator", return_value=MockANMIGValidator()):
            resp = self.client.post(reverse("resolution-detail", kwargs={"pk": r.pk}), {"affirm": "on"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ResolutionSignature.objects.filter(resolution=r).exists())


class ResolutionBreadcrumbTests(_ViewBase):
    """The deep resolution pages (detail, edit) override {% block breadcrumbs %}
    so they render a trail like every other detail page in the app. Without the
    override the registry yields no trail for these untiled sub-routes."""

    def test_detail_page_renders_trail_with_resolution_as_leaf(self):
        res = self._make()
        self.client.force_login(self.member)
        resp = self.client.get(reverse("resolution-detail", kwargs={"pk": res.pk}))
        self.assertContains(resp, 'class="breadcrumbs"')
        self.assertContains(resp, '<a href="/resolutions">Resolutions</a>')
        self.assertContains(resp, '<span aria-current="page">A Resolution</span>')

    def test_edit_page_trail_parents_under_the_resolution(self):
        res = self._make()
        self.client.force_login(self.member)
        resp = self.client.get(reverse("resolution-edit", kwargs={"pk": res.pk}))
        self.assertContains(resp, f'<a href="/resolution/{res.pk}">A Resolution</a>')
        self.assertContains(resp, '<span aria-current="page">Edit</span>')


class SignOnBrowseTests(_ViewBase):
    """The sign-on browse list flags whether the current user has already signed
    each resolution, so they need not open one to find out."""

    def _verifiedSignature(self, res, member):
        ResolutionSignature.objects.create(
            resolution=res, member=member, textHashAtSigning="h",
            verified=True, verificationStatus=MIGStatus.OK,
            checkedAt=datetime.datetime.now(datetime.UTC),
        )

    def test_browse_marks_only_the_resolutions_the_user_signed(self):
        signed = Resolution.objects.create(
            title="Already Signed Petition", kind=Resolution.Kind.GENERAL,
            text="x", proponent=self.member)
        Resolution.objects.create(
            title="Untouched Petition", kind=Resolution.Kind.GENERAL,
            text="x", proponent=self.member)
        signer = UserFactory.make("browse-signer")
        self._verifiedSignature(signed, signer)
        self.client.force_login(signer)
        resp = self.client.get(reverse("sign-resolution"))
        self.assertEqual(resp.status_code, 200)
        # Both still listed; exactly one signed marker and one not-signed marker.
        self.assertContains(resp, "Already Signed Petition")
        self.assertContains(resp, "Untouched Petition")
        self.assertContains(resp, "You signed on", count=1)
        self.assertContains(resp, "You have not signed on yet", count=1)
