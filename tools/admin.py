from django import forms
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin, GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.models import Group
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.urls import reverse
from django.utils.html import format_html
from .models import *

# --- Users & Groups ---------------------------------------------------------
#
# Stock UserAdmin only offers groups on the *change* form, and stock GroupAdmin
# offers no member management at all - so "adding people to groups" required
# creating the user, then knowing to re-open them. Fixed from both sides:
# groups are assignable while creating a user, and members are editable on the
# group page itself.


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Profile", {"fields": ("first_name", "last_name", "email")}),
        ("Groups", {"fields": ("groups",)}),
    )
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "groupNames")

    @admin.display(description="Groups")
    def groupNames(self, obj):
        return ", ".join(group.name for group in obj.groups.all()) or "-"


class GroupAdminForm(forms.ModelForm):
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.order_by("username"),
        required=False,
        widget=FilteredSelectMultiple("users", is_stacked=False),
        label="Users in this group",
        help_text="Move users into the right-hand box and save to update membership.",
    )

    class Meta:
        model = Group
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["users"].initial = self.instance.user_set.all()

    def _save_m2m(self):
        super()._save_m2m()
        self.instance.user_set.set(self.cleaned_data["users"])


class GroupAdmin(DjangoGroupAdmin):
    form = GroupAdminForm
    list_display = ("name", "memberCount")

    @admin.display(description="Members")
    def memberCount(self, obj):
        return obj.user_set.count()


admin.site.unregister(Group)
admin.site.register(Group, GroupAdmin)

admin.site.register(EventOwners)
admin.site.register(PostedEvents)
admin.site.register(DelegatedEvents)


@admin.register(PublishJob)
class PublishJobAdmin(admin.ModelAdmin):
    """Read-only oversight of background publish runs - and the recovery
    surface for a job stuck in RUNNING (worker death mid-publish): inspect the
    payload here, check the Zoom/AN dashboards for partial side effects, and
    decide manually whether to re-submit (precedent: the manual judgment in
    cancel_stuck_delegated_event). Never edit a row - the worker owns them."""

    list_display = ("id", "kindLabel", "statusLabel", "creator", "createdAt", "finishedAt")
    list_filter = ("status", "kind", "createdAt")
    readonly_fields = (
        "kind", "status", "payload", "conflicts", "errorMessage",
        "creator", "owner", "postedEvent", "delegatedEvent",
        "createdAt", "startedAt", "finishedAt",
    )

    @admin.display(description="Kind")
    def kindLabel(self, obj):
        return obj.getKindAsString()

    @admin.display(description="Status")
    def statusLabel(self, obj):
        return obj.getStatusAsString()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AccessRequests)
class AccessRequestsAdmin(admin.ModelAdmin):
    """Read-only oversight of the self-service request queue; decisions happen
    through the review links, not here. Delete is left enabled for spam."""

    list_display = ("getRequesterName", "getTargetDescription", "getStatusAsString", "dateCreated", "getReviewerName")
    list_filter = ("status", "dateCreated")
    readonly_fields = (
        "requester", "group", "permission", "owner", "justification",
        "status", "reviewer", "reason", "dateCreated", "dateReviewed",
    )

    def has_add_permission(self, request):
        return False


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
            return "-"
        url = obj.getPublicUrl()
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Metrics")
    def metricsLink(self, obj):
        # One click from a tree to its click/scan dashboard - the admin
        # otherwise has no path to /link-metrics (opening it still requires
        # the viewLinkMetrics permission).
        if not obj.pk:
            return "-"
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
            return "-"
        url = obj.scanUrl()
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Download QR image")
    def downloadLinks(self, obj):
        if not obj.pk:
            return "-"
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


@admin.register(MembershipSnapshot)
class MembershipSnapshotAdmin(admin.ModelAdmin):
    """Read-only spot-check view - rows are written only by the
    ingest_membership_lists management command (update_or_create keyed on
    listDate), never hand-edited."""

    list_display = ("listDate", "goodStanding", "member", "lapsed", "total", "sourceEmailDate", "ingestedAt")
    ordering = ("listDate",)
    readonly_fields = ("listDate", "goodStanding", "member", "lapsed", "total", "sourceEmailDate", "ingestedAt")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
