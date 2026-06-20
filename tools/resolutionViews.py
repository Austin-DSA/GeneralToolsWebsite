"""Resolutions domain - submission, member sign-on, the public record, and the
Secretary's lifecycle management.

Echo owns the canonical resolution text and enforces the integrity guarantee:
the text locks on the first verified sign-on, and any later edit resets the
sign-ons (see Resolution.replaceText). Each signer is validated live as a Member
in Good Standing against Action Network at sign-on time, via the ANMIGValidator
protocol (the demo box and tests use the deterministic mock; production uses the
live OSDI client when an AN token is configured).

A resolution moves GATHERING -> SCHEDULED -> ADOPTED / REJECTED (or is WITHDRAWN
before a vote, or SUPERSEDED after adoption). Members submit and sign on; the
Secretary drives every transition after that. Adopted text is frozen and handed
off, one-directionally, to the Bylaws-Resolutions repo (see resolutionExport).

The bylaws set the rules by kind: a general resolution needs no sign-ons; a
project committee needs 25 (Section 7.1.5); a bylaws amendment needs proponent +
35 (Section 10.1); a candidate endorsement needs none but a two-thirds vote
(Section 9.2). The single source of that truth is Resolution.Kind.
"""
import datetime
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Exists, OuterRef
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from . import permissions
from .forms import (
    RecordVoteForm, ResolutionEditForm, ResolutionForm, ScheduleForm,
    SupersedeForm, WithdrawForm,
)
from .models import Resolution, ResolutionSignature
from .resolutionExport import repoFilename, resolutionRepoMarkdown
from .resolutionText import normalizedTextHash, renderMarkdown
from .ActionNetworkAPI.migValidator import LiveANMIGValidator, MockANMIGValidator

logger = logging.getLogger(__name__)

BYLAWS_BASE = "https://github.com/Austin-DSA/Bylaws-Resolutions/blob/main/bylaws.md"

# Self-documenting copy for the submit-form type cards. Thresholds/lead-days come
# from Resolution.Kind (the single source of truth); these are just the labels.
_TYPE_LABELS = {
    Resolution.Kind.GENERAL: {
        "thresholdLabel": "No member sign-ons required",
        "deadlineLabel": "submit before the agenda is published",
        "sectionAnchor": "",
    },
    Resolution.Kind.PROJECT_COMMITTEE: {
        "thresholdLabel": "25 member sign-ons",
        "deadlineLabel": "10 days before the meeting",
        "sectionAnchor": "#section-71-project-committees",
    },
    Resolution.Kind.BYLAWS_AMENDMENT: {
        "thresholdLabel": "Proponent + 35 sign-ons",
        "deadlineLabel": "21 days before the GBM",
        "sectionAnchor": "#section-101-notice-of-proposed-amendments",
    },
    Resolution.Kind.CANDIDATE_ENDORSEMENT: {
        "thresholdLabel": "No sign-ons (a two-thirds vote at the meeting)",
        "deadlineLabel": "voted at the meeting",
        "sectionAnchor": "",
    },
}

# The friendly banner a lifecycle action redirects back to the detail page with,
# keyed by the ``?outcome=`` code. (ok, message).
_ACTION_OUTCOMES = {
    "scheduled": (True, "Scheduled. It is on the agenda and closed to new sign-ons."),
    "sentback": (True, "Sent back to gathering sign-ons."),
    "adopted": (True, "Recorded as adopted. The text is frozen and it is now in effect."),
    "rejected": (True, "Recorded as not passed."),
    "withdrawn": (True, "Withdrawn."),
    "superseded": (True, "Marked as superseded."),
    "invalid": (False, "That action could not be completed. Check the form and try again."),
    "illegal": (False, "That action is not allowed from the resolution's current state."),
}


def _typeCards():
    """The type-card context, derived from Resolution.Kind so the cards and the
    model never drift on thresholds."""
    cards = []
    for key, title in Resolution.Kind.CHOICES:
        labels = _TYPE_LABELS.get(key, {})
        anchor = labels.get("sectionAnchor", "")
        cards.append({
            "key": key,
            "title": title,
            "coverage": Resolution.Kind.COVERAGE.get(key, ""),
            "threshold": Resolution.Kind.THRESHOLDS.get(key),
            "thresholdLabel": labels.get("thresholdLabel", ""),
            "deadlineLabel": labels.get("deadlineLabel", ""),
            "section": Resolution.Kind.SECTION.get(key, ""),
            "sectionUrl": BYLAWS_BASE + anchor if anchor else BYLAWS_BASE,
        })
    return cards


def getMIGValidator():
    """The MIG validator to use for a sign-on.

    Live validator only when an AN OSDI token is configured AND we are not in
    DEBUG/DEMO_MODE; otherwise the deterministic mock. The demo box has no AN
    credentials, and tests must never hit the network, so both fall back to the
    mock. Tests override this function to inject specific failure cases."""
    token = None
    try:
        from .SecretManager import SecretManager
        token = SecretManager.getANAPIKey()
    except Exception:
        logger.exception("Could not read the Action Network API token; using mock validator")
    liveEligible = not settings.DEBUG and not getattr(settings, "DEMO_MODE", False)
    if token and liveEligible:
        return LiveANMIGValidator(token)
    if liveEligible:
        # A production-like deploy with no AN token: every sign-on would pass the
        # permissive mock by default. Loud so a misconfigured box is noticed.
        logger.warning(
            "No Action Network token configured outside DEBUG/DEMO_MODE; resolution "
            "sign-on MIG checks are using the permissive mock validator"
        )
    return MockANMIGValidator()


def _paginate(request, queryset, perPage=10):
    """A Paginator page for the current ``?page=``, clamped to a valid page."""
    return Paginator(queryset, perPage).get_page(request.GET.get("page"))


def _filterQuerystring(request):
    """The current querystring minus ``page``, so pager links keep the filters."""
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


@login_required
def submit_resolution(request):
    """The standard submission form. On a valid POST, persists a real Resolution
    and redirects to its detail page."""
    form = ResolutionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        resolution = Resolution.objects.create(
            title=form.cleaned_data["title"],
            kind=form.cleaned_data["kind"],
            text=form.cleaned_data["text"],
            proponent=request.user,
            targetMeeting=form.cleaned_data["targetMeeting"],
        )
        return redirect("resolution-detail", pk=resolution.pk)
    return render(request, "tools/resolutions/submit.html", {
        "form": form,
        "types": _typeCards(),
    })


@login_required
def sign_resolution(request):
    """Browse resolutions currently gathering sign-ons. Each links to its detail
    page, where the actual sign-on happens."""
    mySignOn = ResolutionSignature.objects.filter(
        resolution=OuterRef("pk"), member=request.user,
    )
    resolutions = (
        Resolution.objects.filter(status=Resolution.Status.GATHERING)
        .select_related("proponent", "targetMeeting")
        .annotate(signedByMe=Exists(mySignOn))
    )
    return render(request, "tools/resolutions/browse.html", {
        "resolutions": resolutions,
    })


@login_required
def resolutions_in_effect(request):
    """The public record of adopted resolutions currently governing the chapter
    (adopted and not since superseded). Filterable by kind, paginated."""
    qs = (
        Resolution.objects.filter(
            status=Resolution.Status.ADOPTED, supersededBy__isnull=True,
        )
        .select_related("proponent", "targetMeeting")
        .order_by("-decidedAt", "-createdAt")
    )
    kind = request.GET.get("kind", "")
    if kind in dict(Resolution.Kind.CHOICES):
        qs = qs.filter(kind=kind)
    else:
        kind = ""
    page = _paginate(request, qs)
    return render(request, "tools/resolutions/inEffect.html", {
        "page": page,
        "resolutions": page.object_list,
        "kind": kind,
        "kinds": Resolution.Kind.CHOICES,
        "querystring": _filterQuerystring(request),
    })


@login_required
def resolutions_archive(request):
    """Every resolution, filterable by kind / status / year and sortable.
    Paginated so the list scales as the record grows."""
    qs = Resolution.objects.select_related("proponent", "targetMeeting")

    kind = request.GET.get("kind", "")
    if kind in dict(Resolution.Kind.CHOICES):
        qs = qs.filter(kind=kind)
    else:
        kind = ""

    status = request.GET.get("status", "")
    if status in dict(Resolution.Status.CHOICES):
        qs = qs.filter(status=status)
    else:
        status = ""

    year = request.GET.get("year", "")
    if year.isdigit():
        qs = qs.filter(createdAt__year=int(year))
    else:
        year = ""

    sort = request.GET.get("sort", "newest")
    if sort == "oldest":
        qs = qs.order_by("createdAt")
    elif sort == "title":
        qs = qs.order_by("title")
    else:
        sort = "newest"
        qs = qs.order_by("-createdAt")

    page = _paginate(request, qs)
    return render(request, "tools/resolutions/archive.html", {
        "page": page,
        "resolutions": page.object_list,
        "kind": kind, "status": status, "year": year, "sort": sort,
        "kinds": Resolution.Kind.CHOICES,
        "statuses": Resolution.Status.CHOICES,
        "querystring": _filterQuerystring(request),
    })


def _detailContext(request, resolution, signOutcome=None):
    alreadySigned = ResolutionSignature.objects.filter(
        resolution=resolution, member=request.user,
    ).exists()
    canManage = request.user.has_perm(permissions.ADMINISTER_RESOLUTIONS)
    deadlinePassed = resolution.signOnDeadlinePassed()
    ctx = {
        "resolution": resolution,
        "renderedText": mark_safe(renderMarkdown(resolution.text)),
        "alreadySigned": alreadySigned,
        "canSign": resolution.isOpenForSignOn() and not alreadySigned and not deadlinePassed,
        "deadlinePassed": deadlinePassed,
        "isProponent": resolution.proponent_id == request.user.id,
        "canManage": canManage,
        "signOutcome": signOutcome,
        "events": None,
    }
    if canManage:
        ctx["events"] = list(resolution.events.select_related("actor")[:12])
        ctx["scheduleForm"] = ScheduleForm()
        ctx["voteForm"] = RecordVoteForm()
        ctx["withdrawForm"] = WithdrawForm()
        ctx["supersedeForm"] = SupersedeForm(excludePk=resolution.pk)
        ctx["repoExportName"] = (
            repoFilename(resolution) if resolution.status == Resolution.Status.ADOPTED else ""
        )
    return ctx


@login_required
def resolution_detail(request, pk):
    """The in-Echo read surface: rendered text, live count/threshold/deadline,
    the sign-on action, and (for the Secretary) the lifecycle panel. POST records
    a sign-on after a live MIG check; lifecycle transitions are separate views."""
    resolution = get_object_or_404(Resolution, pk=pk)

    if request.method != "POST":
        signOutcome = None
        code = request.GET.get("outcome")
        if code in _ACTION_OUTCOMES:
            ok, message = _ACTION_OUTCOMES[code]
            signOutcome = {"ok": ok, "message": message}
        return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, signOutcome))

    # --- sign-on ---
    if not resolution.isOpenForSignOn():
        outcome = {"ok": False, "message": "This resolution is not open for sign-ons."}
        return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, outcome))

    if resolution.signOnDeadlinePassed():
        outcome = {"ok": False, "message": "The filing deadline for this resolution has passed."}
        return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, outcome))

    if ResolutionSignature.objects.filter(resolution=resolution, member=request.user).exists():
        outcome = {"ok": True, "message": "You have already signed on to this resolution."}
        return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, outcome))

    if not (request.user.email or "").strip():
        # No email means we cannot run the MIG check at all. Refuse rather than
        # let it slip past (the mock validator would pass an empty address).
        outcome = {"ok": False, "message": "Your account has no email on file, so we cannot verify your membership standing. Add an email to your account, then sign on."}
        return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, outcome))

    result = getMIGValidator().verify(request.user.email)
    if not result.ok:
        # Fail closed: never record an unverified signer as verified.
        outcome = {"ok": False, "message": result.help(), "status": result.status}
        return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, outcome))

    try:
        ResolutionSignature.objects.create(
            resolution=resolution,
            member=request.user,
            textHashAtSigning=normalizedTextHash(resolution.text),
            verified=True,
            verificationStatus=result.status,
            checkedAt=result.checkedAt,
        )
    except IntegrityError:
        outcome = {"ok": True, "message": "You have already signed on to this resolution."}
        return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, outcome))

    # Lock the text on the first sign-on (idempotent). lockText() updates the
    # in-memory instance, so no refresh is needed before re-rendering.
    resolution.lockText()
    outcome = {"ok": True, "message": "Your sign-on is recorded. Thank you."}
    return render(request, "tools/resolutions/detail.html", _detailContext(request, resolution, outcome))


@login_required
def resolution_edit(request, pk):
    """The proponent edits the resolution text. A real change to a locked
    resolution resets its sign-ons (see Resolution.replaceText); we require an
    explicit confirm before applying such a reset."""
    resolution = get_object_or_404(Resolution, pk=pk)
    if resolution.proponent_id != request.user.id and not request.user.has_perm(permissions.ADMINISTER_RESOLUTIONS):
        raise PermissionDenied
    if resolution.status != Resolution.Status.GATHERING:
        raise PermissionDenied  # a scheduled/adopted resolution is read-only

    needsResetConfirm = False
    if request.method == "POST":
        form = ResolutionEditForm(request.POST)
        if form.is_valid():
            newText = form.cleaned_data["text"]
            changesLocked = (
                resolution.locked
                and normalizedTextHash(newText) != resolution.lockedTextHash
            )
            if changesLocked and not form.cleaned_data.get("confirmReset"):
                needsResetConfirm = True
            else:
                resolution.replaceText(newText)
                return redirect("resolution-detail", pk=resolution.pk)
    else:
        form = ResolutionEditForm(initial={"text": resolution.text})

    return render(request, "tools/resolutions/edit.html", {
        "resolution": resolution,
        "form": form,
        "needsResetConfirm": needsResetConfirm,
        "signatureCount": resolution.signatureCount,
    })


@login_required
@permission_required(permissions.ADMINISTER_RESOLUTIONS)
def on_deck(request):
    """The Secretary's On Deck dashboard: resolutions in flight (gathering
    sign-ons or on a meeting agenda), with their bylaws checks and a link to
    manage each one."""
    resolutions = (
        Resolution.objects.filter(status__in=Resolution.Status.IN_FLIGHT)
        .select_related("proponent", "targetMeeting")
    )
    return render(request, "tools/resolutions/status.html", {
        "resolutions": resolutions,
    })


# --- Secretary lifecycle actions (POST-only, permission-gated) -------------
# Each loads the resolution, validates its form, and applies the matching
# Resolution transition method (which enforces the legal-transition guard and
# writes the audit event). All redirect back to the detail page with an
# ``?outcome=`` banner code.

def _actionRedirect(resolution, outcome):
    return redirect(f"{resolution.getUrl()}?outcome={outcome}")


@login_required
@permission_required(permissions.ADMINISTER_RESOLUTIONS)
@require_POST
def resolution_schedule(request, pk):
    resolution = get_object_or_404(Resolution, pk=pk)
    form = ScheduleForm(request.POST)
    if not form.is_valid():
        return _actionRedirect(resolution, "invalid")
    try:
        resolution.schedule(meeting=form.cleaned_data["targetMeeting"], actor=request.user)
    except ValueError:
        return _actionRedirect(resolution, "illegal")
    return _actionRedirect(resolution, "scheduled")


@login_required
@permission_required(permissions.ADMINISTER_RESOLUTIONS)
@require_POST
def resolution_send_back(request, pk):
    resolution = get_object_or_404(Resolution, pk=pk)
    try:
        resolution.sendBackToGathering(actor=request.user)
    except ValueError:
        return _actionRedirect(resolution, "illegal")
    return _actionRedirect(resolution, "sentback")


@login_required
@permission_required(permissions.ADMINISTER_RESOLUTIONS)
@require_POST
def resolution_record_vote(request, pk):
    resolution = get_object_or_404(Resolution, pk=pk)
    form = RecordVoteForm(request.POST)
    if not form.is_valid():
        return _actionRedirect(resolution, "invalid")
    try:
        resolution.recordVote(
            yes=form.cleaned_data["votesYes"],
            no=form.cleaned_data["votesNo"],
            abstain=form.cleaned_data["votesAbstain"],
            actor=request.user,
        )
    except ValueError:
        return _actionRedirect(resolution, "illegal")
    outcome = "adopted" if resolution.status == Resolution.Status.ADOPTED else "rejected"
    return _actionRedirect(resolution, outcome)


@login_required
@permission_required(permissions.ADMINISTER_RESOLUTIONS)
@require_POST
def resolution_withdraw(request, pk):
    resolution = get_object_or_404(Resolution, pk=pk)
    form = WithdrawForm(request.POST)
    note = form.cleaned_data["note"] if form.is_valid() else ""
    try:
        resolution.withdraw(actor=request.user, note=note)
    except ValueError:
        return _actionRedirect(resolution, "illegal")
    return _actionRedirect(resolution, "withdrawn")


@login_required
@permission_required(permissions.ADMINISTER_RESOLUTIONS)
@require_POST
def resolution_supersede(request, pk):
    resolution = get_object_or_404(Resolution, pk=pk)
    form = SupersedeForm(request.POST, excludePk=resolution.pk)
    if not form.is_valid():
        return _actionRedirect(resolution, "invalid")
    try:
        resolution.supersede(
            actor=request.user,
            replacement=form.cleaned_data.get("replacement"),
            note=form.cleaned_data.get("note", ""),
        )
    except ValueError:
        return _actionRedirect(resolution, "illegal")
    return _actionRedirect(resolution, "superseded")


@login_required
@permission_required(permissions.ADMINISTER_RESOLUTIONS)
def resolution_export(request, pk):
    """Download the adopted resolution in the Bylaws-Resolutions repo format, the
    Echo -> repo handoff the Secretary commits by hand."""
    resolution = get_object_or_404(Resolution, pk=pk)
    markdown = resolutionRepoMarkdown(resolution)
    resolution.exportedAt = datetime.datetime.now(datetime.UTC)
    resolution.save(update_fields=["exportedAt"])
    filename = repoFilename(resolution).replace("/", "-")
    response = HttpResponse(markdown, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
