"""Link Tree views.

Three of these are PUBLIC (no login) - the whole point of a link tree is a page
anyone can open, plus the tracked redirect endpoints a click/scan lands on:

    public_tree   GET /t/<slug>/           render the tree (members trees gate on login)
    go            GET /go/<item_id>/       log a WEB click, 302 to the destination
    qr_redirect   GET /qr/<code>/          log a QR scan, 302 to the QR target

The rest are permission-gated (maintainers / metrics viewers):

    qr_image      GET /qr/<code>/image     generate the QR graphic (svg|png)   [manageLinkTree]
    link_metrics  GET /link-metrics[/<slug>]  analytics dashboard             [viewLinkMetrics]
    link_metrics_csv GET /link-metrics/<slug>.csv  event export               [viewLinkMetrics]

Redirect targets are always admin-controlled (a stored tree/item/QR target),
never taken from a query parameter, so there is no open-redirect surface.
"""

import csv
import io
import logging

import segno

from django.contrib.auth.decorators import permission_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render

from . import permissions
from .LinkTree import metrics, tracking
from .models import LinkEvent, LinkTree, LinkTreeItem, QRCode

logger = logging.getLogger(__name__)


# MARK: Public pages


def _gateMembersTree(request, tree):
    """Return a login redirect if the tree is members-only and the user isn't
    authenticated; otherwise None. Public trees are always allowed."""
    if tree.isMembersOnly() and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    return None


def public_tree(request, slug):
    tree = get_object_or_404(
        LinkTree.objects.prefetch_related("items"), slug=slug, isActive=True
    )
    gate = _gateMembersTree(request, tree)
    if gate is not None:
        return gate

    # Show section headers plus any link with a working destination. A wiki item
    # that hasn't resolved yet (or a manual link with no url) is skipped rather
    # than rendered as a dead button.
    items = [item for item in tree.activeItems() if item.shouldDisplay()]
    return render(request, "linktree/tree.html", {"tree": tree, "items": items})


def go(request, item_id):
    """Log a web click and redirect to the item's destination."""
    item = get_object_or_404(
        LinkTreeItem.objects.select_related("tree"), pk=item_id, isActive=True
    )
    if not item.tree.isActive:
        raise Http404("link tree is inactive")
    gate = _gateMembersTree(request, item.tree)
    if gate is not None:
        return gate

    destination = item.destinationUrl()
    if not destination:
        raise Http404("link has no destination yet")

    tracking.recordEvent(
        request,
        source=LinkEvent.Source.WEB,
        tree=item.tree,
        item=item,
        destinationUrl=destination,
    )
    return HttpResponseRedirect(destination)


def qr_redirect(request, code):
    """Log a QR scan and redirect to the code's current target (repointable)."""
    qr = get_object_or_404(QRCode, code=code, isActive=True)
    destination, tree, item = qr.resolveTarget()
    if not destination:
        raise Http404("QR code has no target yet")

    # Honor the members-only wall the same way go()/public_tree() do: a QR whose
    # target resolves into a MEMBERS tree must not 302 an anonymous scanner
    # straight to the destination. resolveTarget() gives us the owning tree for
    # both tree- and item-targets; a rawUrl target has no tree and is public by
    # design.
    if tree is not None:
        gate = _gateMembersTree(request, tree)
        if gate is not None:
            return gate

    tracking.recordEvent(
        request,
        source=LinkEvent.Source.QR,
        tree=tree,
        item=item,
        qr=qr,
        destinationUrl=destination,
    )
    return HttpResponseRedirect(destination)


# MARK: QR image generation (maintainers only)


@permission_required(permissions.MANAGE_LINK_TREE)
def qr_image(request, code):
    """Render the QR graphic that encodes this code's /qr/<code>/ scan URL.

    Encoding the scan URL (not the destination) is what makes a printed code
    repointable and every scan trackable. ?fmt=png|svg (default svg);
    ?download=1 sends it as an attachment for printing.
    """
    qr = get_object_or_404(QRCode, code=code)
    scanUrl = request.build_absolute_uri(qr.scanUrl())

    fmt = (request.GET.get("fmt") or "svg").lower()
    if fmt not in ("svg", "png"):
        fmt = "svg"

    image = segno.make(scanUrl, error="m")
    buffer = io.BytesIO()
    if fmt == "png":
        image.save(buffer, kind="png", scale=10, border=2)
        contentType = "image/png"
    else:
        image.save(buffer, kind="svg", scale=10, border=2)
        contentType = "image/svg+xml"

    response = HttpResponse(buffer.getvalue(), content_type=contentType)
    disposition = "attachment" if request.GET.get("download") else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="qr-{qr.code}.{fmt}"'
    return response


# MARK: Metrics dashboard (viewers only)
#
# All aggregation lives in LinkTree/metrics.py; these handlers just gate, fetch,
# and render.


@permission_required(permissions.VIEW_LINK_METRICS)
def link_metrics(request, slug=None):
    if slug is None:
        return render(request, "tools/link_metrics.html", {"overview": metrics.overviewRows()})

    tree = get_object_or_404(LinkTree, slug=slug)
    context = metrics.treeSummary(tree)
    context["tree"] = tree
    context["series"] = metrics.dailySeries(LinkEvent.objects.filter(tree=tree))
    context["windowDays"] = metrics.METRICS_WINDOW_DAYS
    return render(request, "tools/link_metrics.html", context)


@permission_required(permissions.VIEW_LINK_METRICS)
def link_metrics_csv(request, slug):
    """Per-day, per-source aggregate export for a tree (privacy-safe - no PII)."""
    tree = get_object_or_404(LinkTree, slug=slug)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="link-metrics-{tree.slug}.csv"'
    writer = csv.writer(response)
    writer.writerow(["date", "source", "events"])
    labels = {LinkEvent.Source.WEB: "web", LinkEvent.Source.QR: "qr"}
    for row in metrics.dailyEventTotals(LinkEvent.objects.filter(tree=tree)):
        writer.writerow([row["day"], labels.get(row["source"], row["source"]), row["total"]])
    return response
