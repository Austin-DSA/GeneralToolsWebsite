from django.contrib.auth.admin import UserAdmin
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from .models import *
# Register your models here.
admin.site.register(User, UserAdmin)
admin.site.register(EventOwners)
admin.site.register(PostedEvents)
admin.site.register(DelegatedEvents)


# --- Link Tree -------------------------------------------------------------
#
# This admin IS the maintenance UI for novices: a LinkTree page with its items
# edited inline, ordered by the `order` integer. RBAC is the standard Django
# group/permission system (give maintainers the manageLinkTree permission).


class LinkTreeItemInline(admin.StackedInline):
    # Stacked (not tabular) so each field's help_text shows as visible text under
    # the field, rather than as a hard-to-hit hover tooltip on a tiny icon.
    model = LinkTreeItem
    extra = 1
    ordering = ("order",)
    fields = (
        "order", "isActive", "kind", "icon", "label", "subtitle",
        "url", "wikiMode", "wikiQuery", "pinnedWikiDocId", "resolvedLabel",
    )
    readonly_fields = ("resolvedLabel",)


@admin.register(LinkTree)
class LinkTreeAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "visibility", "isActive", "publicLink", "metricsLink")
    list_filter = ("visibility", "isActive")
    prepopulated_fields = {"slug": ("title",)}
    inlines = (LinkTreeItemInline,)
    readonly_fields = ("publicLink", "metricsLink", "dateCreated", "dateModified")

    @admin.display(description="Public page")
    def publicLink(self, obj):
        if not obj.pk:
            return "—"
        url = obj.getPublicUrl()
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Metrics")
    def metricsLink(self, obj):
        # One click from a tree to its click/scan dashboard — the admin
        # otherwise has no path to /link-metrics (opening it still requires
        # the viewLinkMetrics permission).
        if not obj.pk:
            return "—"
        url = reverse("link-metrics-tree", kwargs={"slug": obj.slug})
        return format_html('<a href="{}" target="_blank">View metrics</a>', url)


@admin.register(LinkTreeItem)
class LinkTreeItemAdmin(admin.ModelAdmin):
    list_display = ("__str__", "tree", "kind", "order", "isActive", "isResolved")
    list_filter = ("kind", "isActive", "tree")
    readonly_fields = ("resolvedUrl", "resolvedLabel", "resolvedAt")


@admin.register(QRCode)
class QRCodeAdmin(admin.ModelAdmin):
    list_display = ("label", "code", "campaign", "isActive", "scanLink")
    list_filter = ("isActive", "campaign")
    prepopulated_fields = {"code": ("label",)}
    readonly_fields = ("scanLink", "downloadLinks", "createdBy", "dateCreated", "dateModified")

    def save_model(self, request, obj, form, change):
        if not change and obj.createdBy_id is None:
            obj.createdBy = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Scan URL (what the QR encodes)")
    def scanLink(self, obj):
        if not obj.pk:
            return "—"
        url = obj.scanUrl()
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Download QR image")
    def downloadLinks(self, obj):
        if not obj.pk:
            return "—"
        base = reverse("qr-image", kwargs={"code": obj.code})
        return format_html(
            '<a href="{}?fmt=svg&download=1" target="_blank">SVG</a> · '
            '<a href="{}?fmt=png&download=1" target="_blank">PNG</a> · '
            '<a href="{}?fmt=svg" target="_blank">preview</a>',
            base, base, base,
        )


@admin.register(LinkEvent)
class LinkEventAdmin(admin.ModelAdmin):
    """Read-only spot-check view; real analysis lives in the metrics dashboard."""
    list_display = ("occurredAt", "get_source_display", "tree", "item", "qr", "uaFamily", "referrerHost")
    list_filter = ("source", "tree", "occurredAt")
    readonly_fields = (
        "tree", "item", "qr", "source", "occurredAt",
        "destinationUrl", "visitorHash", "uaFamily", "referrerHost",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
