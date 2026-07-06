"""Member-in-Good-Standing (MIG) validation, decoupled from the live client.

The sign-on view depends on the ``ANMIGValidator`` protocol, never on
``ActionNetworkAPI`` directly, for two reasons:

  * the live client runs a network call in ``__init__`` (endpoint discovery), so
    it cannot be constructed in tests/demo - the mock validator stands in;
  * the demo box has no Action Network credentials, so the factory
    (``tools.resolutionViews.getMIGValidator``) returns the mock there too.

Framework-free (no Django imports). The verification status codes here are the
single source of truth: ``ResolutionSignature.VerificationStatus`` imports them,
so the model and the validator never drift.

Gate semantics match the voting branch (``tools/voting/tasks.py`` on
``garrigan/voting``): ``memberStatus`` (the ``actionkit_is_member_in_good_standing``
flag) is the authoritative MIG gate; chapter/expiry are inspected only on the
false branch to produce a specific, actionable reason. Tightening to a
conjunctive gate (require austin chapter AND not expired on top of the flag) is a
one-line change here, pending Garrigan's call.
"""
import dataclasses
import datetime
import typing

from . import ActionNetworkAPI


class MIGStatus:
    """Verification outcome codes. Stored on ResolutionSignature.verificationStatus."""

    OK = "OK"
    NOT_MEMBER = "NOT_MEMBER"
    EXPIRED = "EXPIRED"
    WRONG_CHAPTER = "WRONG_CHAPTER"
    NOT_FOUND = "NOT_FOUND"
    MULTIPLE = "MULTIPLE"
    MISSING_FIELDS = "MISSING_FIELDS"
    API_ERROR = "API_ERROR"

    # (value, label) pairs for the model's choices=.
    CHOICES = (
        (OK, "Verified member in good standing"),
        (NOT_MEMBER, "Not a member in good standing"),
        (EXPIRED, "Membership has lapsed"),
        (WRONG_CHAPTER, "Not an Austin chapter member"),
        (NOT_FOUND, "No matching record found"),
        (MULTIPLE, "Multiple conflicting records found"),
        (MISSING_FIELDS, "Membership record is missing required fields"),
        (API_ERROR, "Could not reach Action Network"),
    )


# Actionable, member-facing messages (no em-dashes per the house convention).
# Adapted from the voting branch's getVerificationErrorHelp, reframed for the
# sign-on moment (the signer is still inside the gathering window, so the
# message tells them how to fix it and sign again).
_HELP = {
    MIGStatus.OK: "",
    MIGStatus.NOT_MEMBER: (
        "We could not confirm an active membership for this account's email. "
        "If you are not a member you can join at https://act.dsausa.org/donate/membership/. "
        "If you believe this is wrong, contact membership@austindsa.org."
    ),
    MIGStatus.EXPIRED: (
        "Our records show your membership has lapsed. You can recommit at "
        "https://act.dsausa.org/donate/membership/ and then sign on again. "
        "If you are current on dues, contact membership@austindsa.org."
    ),
    MIGStatus.WRONG_CHAPTER: (
        "This account's membership is recorded under a different chapter, so it "
        "cannot sign on to an Austin DSA resolution. Contact membership@austindsa.org "
        "if that looks wrong."
    ),
    MIGStatus.NOT_FOUND: (
        "We could not find this account's email in our membership list. This usually "
        "means the account uses a different email than your membership. Update your "
        "account email to the one on your membership, or contact membership@austindsa.org."
    ),
    MIGStatus.MULTIPLE: (
        "We found more than one membership record for this email, which we cannot "
        "resolve automatically. Please contact membership@austindsa.org."
    ),
    MIGStatus.MISSING_FIELDS: (
        "Your membership record is missing fields we need to verify good standing. "
        "Please contact membership@austindsa.org."
    ),
    MIGStatus.API_ERROR: (
        "We could not verify your standing right now. Please try again in a minute."
    ),
}


def helpText(status: str) -> str:
    return _HELP.get(status, _HELP[MIGStatus.API_ERROR])


@dataclasses.dataclass
class MIGCheckResult:
    ok: bool          # True only when the signer is a verified MIG
    status: str       # one of MIGStatus.*
    checkedAt: datetime.datetime

    def help(self) -> str:
        return helpText(self.status)


class ANMIGValidator(typing.Protocol):
    def verify(self, email: str) -> MIGCheckResult: ...


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class LiveANMIGValidator:
    """Wraps the live OSDI client. The client is built lazily on first verify so
    constructing the validator (e.g. in the factory) never hits the network -
    only an actual sign-on does. Fails closed: any error maps to API_ERROR with
    ok=False, so an unverified signer is never recorded as verified."""

    def __init__(self, apiKey: str) -> None:
        self._apiKey = apiKey
        self._client = None

    def _getClient(self):
        if self._client is None:
            self._client = ActionNetworkAPI.ActionNetworkAPI(self._apiKey)
        return self._client

    def verify(self, email: str) -> MIGCheckResult:
        try:
            client = self._getClient()
            apiStatus, person = client.getPersonForVoteValidation(email)
        except Exception:
            return MIGCheckResult(ok=False, status=MIGStatus.API_ERROR, checkedAt=_now())

        Status = ActionNetworkAPI.GetPersonAPIReturnStatus
        if apiStatus == Status.NOT_FOUND:
            return MIGCheckResult(False, MIGStatus.NOT_FOUND, _now())
        if apiStatus == Status.MULTIPLE_RECORDS_RETURNED:
            return MIGCheckResult(False, MIGStatus.MULTIPLE, _now())
        if apiStatus == Status.MISSING_REQUIRED_CUSTOM_FIELDS:
            return MIGCheckResult(False, MIGStatus.MISSING_FIELDS, _now())
        if apiStatus != Status.SUCCESS or person is None:
            return MIGCheckResult(False, MIGStatus.API_ERROR, _now())

        # SUCCESS: memberStatus is the authoritative MIG flag; chapter/expiry
        # only refine the failure reason on the false branch.
        if person.memberStatus:
            return MIGCheckResult(True, MIGStatus.OK, _now())
        if str(person.chapter).lower() != "austin":
            return MIGCheckResult(False, MIGStatus.WRONG_CHAPTER, _now())
        if person.expireDate < datetime.date.today():
            return MIGCheckResult(False, MIGStatus.EXPIRED, _now())
        return MIGCheckResult(False, MIGStatus.NOT_MEMBER, _now())


class MockANMIGValidator:
    """Deterministic validator for DEBUG / DEMO_MODE / tests (no network).

    By default every email passes (so seeded demo members can sign on). Pass
    ``statusByEmail`` to force specific outcomes per email, and/or
    ``defaultStatus`` to flip the default. Email match is case-insensitive."""

    def __init__(self, defaultStatus: str = MIGStatus.OK, statusByEmail: dict | None = None) -> None:
        self.defaultStatus = defaultStatus
        self.statusByEmail = {k.lower(): v for k, v in (statusByEmail or {}).items()}

    def verify(self, email: str) -> MIGCheckResult:
        status = self.statusByEmail.get((email or "").lower(), self.defaultStatus)
        return MIGCheckResult(ok=(status == MIGStatus.OK), status=status, checkedAt=_now())
