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

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import redirect_to_login
from django.db import models, transaction
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render

from . import permissions
from .forms import LinkTreeItemForm, LinkTreeSettingsForm, QRCodeForm
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


# MARK: Link Tree management (maintainers only)
#
# The in-app management surface, gated solely on manageLinkTree (no is_staff).
# Form validation is the authoritative guard: views assign cleaned values field
# by field and save - they never call model full_clean()/clean(), and they never
# write the wiki resolve cache (resolution stays out-of-band).


def _formatVisibleWindow(value):
    """Render a stored UTC datetime for the datetime-local widget without any
    localization (the field documents itself as UTC)."""
    if value is None:
        return ""
    return value.strftime("%Y-%m-%dT%H:%M")


@login_required
@permission_required(permissions.MANAGE_LINK_TREE)
def manage_link_tree_list(request):
    createForm = LinkTreeSettingsForm()
    if request.method == "POST":
        createForm = LinkTreeSettingsForm(request.POST)
        if createForm.is_valid():
            tree = LinkTree()
            tree.slug = createForm.cleaned_data[LinkTreeSettingsForm.Keys.SLUG]
            tree.title = createForm.cleaned_data[LinkTreeSettingsForm.Keys.TITLE]
            tree.description = createForm.cleaned_data[LinkTreeSettingsForm.Keys.DESCRIPTION]
            tree.visibility = createForm.cleaned_data[LinkTreeSettingsForm.Keys.VISIBILITY]
            tree.isActive = createForm.cleaned_data[LinkTreeSettingsForm.Keys.IS_ACTIVE]
            tree.save()
            logger.info(
                "ManageLinkTree: %s created link tree '%s'",
                request.user.get_username(), tree.slug,
            )
            return redirect("manage-link-tree-edit", treeId=tree.pk)

    treeRows = [
        {
            "tree": tree,
            "itemCount": tree.items.count(),
            "qrCount": tree.qrCodes.count(),
        }
        for tree in LinkTree.objects.order_by("title")
    ]
    return render(request, "tools/manage-link-trees/list.html", {
        "treeRows": treeRows,
        "createForm": createForm,
    })


@login_required
@permission_required(permissions.MANAGE_LINK_TREE)
def manage_link_tree_edit(request, treeId):
    tree = get_object_or_404(LinkTree, pk=treeId)
    treeSaved = False
    if request.method == "POST":
        form = LinkTreeSettingsForm(request.POST, tree=tree)
        if form.is_valid():
            tree.slug = form.cleaned_data[LinkTreeSettingsForm.Keys.SLUG]
            tree.title = form.cleaned_data[LinkTreeSettingsForm.Keys.TITLE]
            tree.description = form.cleaned_data[LinkTreeSettingsForm.Keys.DESCRIPTION]
            tree.visibility = form.cleaned_data[LinkTreeSettingsForm.Keys.VISIBILITY]
            tree.isActive = form.cleaned_data[LinkTreeSettingsForm.Keys.IS_ACTIVE]
            tree.save()
            logger.info(
                "ManageLinkTree: %s edited link tree '%s'",
                request.user.get_username(), tree.slug,
            )
            treeSaved = True
    else:
        form = LinkTreeSettingsForm(tree=tree, initial={
            LinkTreeSettingsForm.Keys.SLUG: tree.slug,
            LinkTreeSettingsForm.Keys.TITLE: tree.title,
            LinkTreeSettingsForm.Keys.DESCRIPTION: tree.description,
            LinkTreeSettingsForm.Keys.VISIBILITY: tree.visibility,
            LinkTreeSettingsForm.Keys.IS_ACTIVE: tree.isActive,
        })

    return render(request, "tools/manage-link-trees/tree.html", {
        "tree": tree,
        "form": form,
        "treeSaved": treeSaved,
        "items": tree.items.all(),
        "qrCodes": tree.qrCodes.all(),
    })


@login_required
@permission_required(permissions.MANAGE_LINK_TREE)
def manage_link_tree_item_edit(request, treeId, itemId=None):
    tree = get_object_or_404(LinkTree, pk=treeId)
    if itemId is None:
        item = None
    else:
        item = get_object_or_404(LinkTreeItem, pk=itemId, tree_id=treeId)

    if request.method == "POST":
        form = LinkTreeItemForm(request.POST)
        if form.is_valid():
            if item is None:
                item = LinkTreeItem(tree=tree)
            item.kind = form.cleaned_data[LinkTreeItemForm.Keys.KIND]
            item.order = form.cleaned_data[LinkTreeItemForm.Keys.ORDER]
            item.icon = form.cleaned_data[LinkTreeItemForm.Keys.ICON]
            item.label = form.cleaned_data[LinkTreeItemForm.Keys.LABEL]
            item.subtitle = form.cleaned_data[LinkTreeItemForm.Keys.SUBTITLE]
            item.url = form.cleaned_data[LinkTreeItemForm.Keys.URL]
            item.isActive = form.cleaned_data[LinkTreeItemForm.Keys.IS_ACTIVE]
            item.visibleFrom = form.cleaned_data[LinkTreeItemForm.Keys.VISIBLE_FROM]
            item.visibleUntil = form.cleaned_data[LinkTreeItemForm.Keys.VISIBLE_UNTIL]
            item.wikiMode = (
                form.cleaned_data[LinkTreeItemForm.Keys.WIKI_MODE]
                if form.cleaned_data[LinkTreeItemForm.Keys.WIKI_MODE] != ""
                else LinkTreeItem.WikiMode.LATEST_MATCH
            )
            item.wikiQuery = form.cleaned_data[LinkTreeItemForm.Keys.WIKI_QUERY]
            item.wikiCollectionId = form.cleaned_data[LinkTreeItemForm.Keys.WIKI_COLLECTION_ID]
            item.pinnedWikiDocId = form.cleaned_data[LinkTreeItemForm.Keys.PINNED_WIKI_DOC_ID]
            item.save()
            logger.info(
                "ManageLinkTree: %s saved item %s on tree '%s'",
                request.user.get_username(), item.pk, tree.slug,
            )
            return redirect("manage-link-tree-edit", treeId=treeId)
    else:
        if item is None:
            defaultOrder = (tree.items.aggregate(maxOrder=models.Max("order"))["maxOrder"] or -1) + 1
            form = LinkTreeItemForm(initial={
                LinkTreeItemForm.Keys.KIND: LinkTreeItem.Kind.MANUAL,
                LinkTreeItemForm.Keys.ORDER: defaultOrder,
                LinkTreeItemForm.Keys.IS_ACTIVE: True,
                LinkTreeItemForm.Keys.WIKI_MODE: LinkTreeItem.WikiMode.LATEST_MATCH,
            })
        else:
            form = LinkTreeItemForm(initial={
                LinkTreeItemForm.Keys.KIND: item.kind,
                LinkTreeItemForm.Keys.ORDER: item.order,
                LinkTreeItemForm.Keys.ICON: item.icon,
                LinkTreeItemForm.Keys.LABEL: item.label,
                LinkTreeItemForm.Keys.SUBTITLE: item.subtitle,
                LinkTreeItemForm.Keys.URL: item.url,
                LinkTreeItemForm.Keys.IS_ACTIVE: item.isActive,
                LinkTreeItemForm.Keys.VISIBLE_FROM: _formatVisibleWindow(item.visibleFrom),
                LinkTreeItemForm.Keys.VISIBLE_UNTIL: _formatVisibleWindow(item.visibleUntil),
                LinkTreeItemForm.Keys.WIKI_MODE: item.wikiMode,
                LinkTreeItemForm.Keys.WIKI_QUERY: item.wikiQuery,
                LinkTreeItemForm.Keys.WIKI_COLLECTION_ID: item.wikiCollectionId,
                LinkTreeItemForm.Keys.PINNED_WIKI_DOC_ID: item.pinnedWikiDocId,
            })

    return render(request, "tools/manage-link-trees/item.html", {
        "tree": tree,
        "item": item,
        "form": form,
    })


@login_required
@permission_required(permissions.MANAGE_LINK_TREE)
def manage_link_tree_item_reorder(request, treeId):
    if request.method != "POST":
        return redirect("manage-link-tree-edit", treeId=treeId)

    # Discard anything that isn't an int; the redirect target is never read from
    # a param, so there is no open-redirect surface here.
    submittedIds = []
    for raw in request.POST.getlist("itemOrder"):
        try:
            submittedIds.append(int(raw))
        except (TypeError, ValueError):
            continue

    if not submittedIds:
        # Empty/garbage POST: nothing to reorder, no writes.
        return redirect("manage-link-tree-edit", treeId=treeId)

    with transaction.atomic():
        treeItems = list(
            LinkTreeItem.objects.filter(tree_id=treeId).order_by("order", "id")
        )
        treeItemIds = {item.id for item in treeItems}

        # Submitted ids that belong to this tree, in submitted order; then the
        # rest of the tree's items in their prior relative order. This densely
        # renumbers ALL items 0..n-1 in one pass (no stale-order ties).
        orderedIds = [itemId for itemId in submittedIds if itemId in treeItemIds]
        seen = set(orderedIds)
        for item in treeItems:
            if item.id not in seen:
                orderedIds.append(item.id)
                seen.add(item.id)

        newOrderByItemId = {itemId: index for index, itemId in enumerate(orderedIds)}
        for item in treeItems:
            item.order = newOrderByItemId[item.id]
        # bulk_update only touches "order"; LinkTreeItem has no auto_now or save
        # signal, so this is lossless.
        LinkTreeItem.objects.bulk_update(treeItems, ["order"])

    return redirect("manage-link-tree-edit", treeId=treeId)


@login_required
@permission_required(permissions.MANAGE_LINK_TREE)
def manage_qr_code_list(request):
    qrRows = [
        {"qr": qr, "scanUrl": qr.scanUrl(), "targetUrl": qr.targetUrl()}
        for qr in QRCode.objects.select_related("tree", "item").order_by("label")
    ]
    return render(request, "tools/manage-link-trees/qr-list.html", {"qrRows": qrRows})


@login_required
@permission_required(permissions.MANAGE_LINK_TREE)
def manage_qr_code_edit(request, code=None):
    if code is None:
        qr = None
    else:
        qr = get_object_or_404(QRCode, code=code)

    if request.method == "POST":
        form = QRCodeForm(request.POST, qr=qr)
        if form.is_valid():
            if qr is None:
                qr = QRCode()
                qr.createdBy = request.user
            qr.code = form.cleaned_data["code"]
            qr.label = form.cleaned_data["label"]
            qr.campaign = form.cleaned_data["campaign"]
            qr.tree = form.cleaned_data["tree"]
            qr.item = form.cleaned_data["item"]
            qr.rawUrl = form.cleaned_data["rawUrl"]
            qr.isActive = form.cleaned_data["isActive"]
            qr.save()
            logger.info(
                "ManageLinkTree: %s saved QR code '%s'",
                request.user.get_username(), qr.code,
            )
            return redirect("manage-qr-code-list")
    else:
        if qr is None:
            form = QRCodeForm(initial={"isActive": True})
        else:
            form = QRCodeForm(qr=qr, initial={
                "code": qr.code,
                "label": qr.label,
                "campaign": qr.campaign,
                "tree": qr.tree_id,
                "item": qr.item_id,
                "rawUrl": qr.rawUrl,
                "isActive": qr.isActive,
            })

    return render(request, "tools/manage-link-trees/qr.html", {
        "qr": qr,
        "form": form,
    })
