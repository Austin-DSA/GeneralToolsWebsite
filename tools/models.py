from django.db import models
from django.contrib.auth.models import AbstractUser, Group, Permission
from django.utils import timezone as djangoTimezone
from .EventAutomation.EventAutomationDriver import EventInfo, ActionNetworkAutomation
import datetime
import pytz
from django.urls import reverse
from . import permissions
from . import utils

# Approving a committee (EventOwner) join grants the full event-lead capability
# through this managed role group. The EventOwner's authorizer list scopes which
# owner the member may act for; this group carries the page-level event
# permissions that the scoping is otherwise inert without. The lead bundle is
# publish + approve delegated events (plus the two list views needed to use them).
#
# Garrigan's call (2026-06): bundle committee membership with the lead capability
# for now, but keep the grant structured as two separable steps (see grantTo) so
# we can split them later - e.g. if enough non-lead members start joining that
# membership should stop implying publish/approve rights. To separate, stop
# granting EVENT_LEAD_ROLE_GROUP on join and grant the narrower publish-only
# "Event Publishers" group (and "Event Approvers" for approvers) instead.
EVENT_LEAD_ROLE_GROUP = "Event Leads"
EVENT_LEAD_PERMISSIONS = (
    permissions.PUBLISH_EVENT,
    permissions.VIEW_PUBLISHED_EVENTS,
    permissions.VIEW_DELEGATED_EVENTS,
    permissions.APPROVE_DELEGATED_EVENT,
)


class User(AbstractUser):
    def getUserNameString(self) -> str:
        return f"{self.first_name} {self.last_name} - {self.email}"

    def getDisplayName(self) -> str:
        """Compact name for list tables (detail pages keep name + email)."""
        fullName = f"{self.first_name} {self.last_name}".strip()
        return fullName or self.username

class EventOwners(models.Model):
    name = models.CharField(max_length=100, unique=True)
    authorizers = models.ManyToManyField(User, related_name="eventAuthorizations")
    expiration = models.DateTimeField()
    isPermanent = models.BooleanField(default=False)
    def isActive(self):
        if self.isPermanent or datetime.datetime.now(datetime.UTC) < self.expiration:
            return True
        return False

    def __str__(self):
        return self.name

# List of all previously created events
# All date-times are in UTC
class PostedEvents(models.Model):
    title = models.CharField(max_length=500)
    start = models.DateTimeField()
    end = models.DateTimeField()
    timezone = models.CharField(max_length=50)

    locationName = models.CharField(max_length=500)
    streetAddress = models.CharField(max_length=500)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    zip = models.CharField(max_length=10)
    country = models.CharField(max_length=100)

    description = models.TextField()
    instructions = models.TextField()

    dateCreated = models.DateTimeField()
    datePublished = models.DateTimeField()

    # Not sure how big the links can get so using text fields
    anManageLink = models.TextField()
    anShareLink = models.TextField()
    gCalLink = models.TextField()
    zoomLink = models.TextField()
    zoomAccount = models.CharField(max_length=100)
    zoomRequired = models.BooleanField(default=True)

    creator = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="postedEventCreator")
    authorizer = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="postedEventAuthorizer")
    reason = models.TextField()

    owner = models.ForeignKey(EventOwners, on_delete=models.SET_NULL, blank=True, null=True)

    def getCreatorName(self) -> str:
        if self.creator is None:
            return ""
        return self.creator.getUserNameString()
    
    def getApproverName(self) -> str:
        if self.authorizer is None:
            return ""
        return self.authorizer.getUserNameString()
    
    def getOwnerName(self) -> str:
        if self.owner is None:
            return ""
        return self.owner.name
    
    def getUrl(self) -> str:
        return reverse("event-detail", kwargs={"pk" : self.id})
    
    def getStartLocalizedStr(self) -> str:
        return self.getStartLocalized().strftime(utils.DATE_TIME_FORMAT)
    
    def getEndLocalizedStr(self) -> str:
        return self.getEndLocalized().strftime(utils.DATE_TIME_FORMAT)

    def getStartLocalized(self) -> datetime.datetime:
        utcTime = self.start
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.astimezone(timezone)
        return localTime
    
    def getEndLocalized(self) -> datetime.datetime:
        utcTime = self.end
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.astimezone(timezone)
        return localTime
    
    def getEventInfo(self) -> EventInfo:
        return EventInfo(title=self.title,
                         start=self.getStartLocalized(),
                         end=self.getEndLocalized(),
                         locationName=self.locationName,
                         streetAddress=self.streetAddress,
                         city=self.city,
                         state=self.state,
                         zip=self.zip,
                         description=self.description,
                         instructions=self.instructions,
                         country=self.country,
                         zoomRequired=self.zoomRequired)


 # List of events that have been created to be delegated to an authorizer
 # There will be duplication with approved events here and the events in PostedEvents, PostedEvents should be the truth of all published events
class DelegatedEvents(models.Model):
    class Status:
        REQUESTED = 0
        DENIED = 1
        APPROVED = 2

    title = models.CharField(max_length=500)
    start = models.DateTimeField()
    end = models.DateTimeField()
    timezone = models.CharField(max_length=50)

    locationName = models.CharField(max_length=500)
    streetAddress = models.CharField(max_length=500)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    zip = models.CharField(max_length=10)
    country = models.CharField(max_length=100)

    description = models.TextField()
    instructions = models.TextField()

    dateCreated = models.DateTimeField()
    dateReviewed = models.DateTimeField(null=True, blank=True, default=None)

    creator = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="delegatedEventCreator")
    owner = models.ForeignKey(EventOwners, on_delete=models.SET_NULL, blank=True, null=True)
    approver = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="delegatedEventApprover")

    status = models.IntegerField()
    reason = models.TextField(blank=True)

    zoomRequired = models.BooleanField(default=True)

    eventType = models.IntegerField(default=ActionNetworkAutomation.ANTypes.HYBRID)

    def getStatusAsString(self) -> str:
        if self.status == DelegatedEvents.Status.REQUESTED:
            return "Requested"
        elif self.status == DelegatedEvents.Status.DENIED:
            return "Denied"
        elif self.status == DelegatedEvents.Status.APPROVED:
            return "Approved"
        else:
            return f"Unkown {self.status}"
    
    def getCreatorName(self) -> str:
        if self.creator is None:
            return ""
        return self.creator.getUserNameString()
    
    def getApproverName(self) -> str:
        if self.approver is None:
            return ""
        return self.approver.getUserNameString()
    
    def getOwnerName(self) -> str:
        if self.owner is None:
            return ""
        return self.owner.name
    
    def canBeApprovedBy(self, user) -> bool:
        # Mirrors the two gates on approve_delegated_event: the Django
        # permission plus membership in the owner's authorizers.
        if self.status != DelegatedEvents.Status.REQUESTED or self.owner is None:
            return False
        return (user.has_perm(permissions.APPROVE_DELEGATED_EVENT)
                and self.owner.authorizers.filter(id=user.id).exists())

    def getUrlFor(self, user) -> str:
        # Send the viewer to the approve page only if they can actually act on
        # the request; everyone else gets the read-only detail page (the
        # approve view rejects them anyway).
        if self.canBeApprovedBy(user):
            return reverse("approve-delegated-event", kwargs={ "id" :self.id})
        return reverse("delegated-event-detail", kwargs={"pk" : self.id})

    def getStartLocalizedStr(self) -> str:
        return self.getStartLocalized().strftime(utils.DATE_TIME_FORMAT)

    def getEndLocalizedStr(self) -> str:
        return self.getEndLocalized().strftime(utils.DATE_TIME_FORMAT)

    def getStartLocalized(self) -> datetime.datetime:
        utcTime = self.start
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.astimezone(timezone)
        return localTime

    def getEndLocalized(self) -> datetime.datetime:
        utcTime = self.end
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.astimezone(timezone)
        return localTime

    def getEventInfo(self) -> EventInfo:
        return EventInfo(title=self.title,
                         start=self.getStartLocalized(),
                         end=self.getEndLocalized(),
                         locationName=self.locationName,
                         streetAddress=self.streetAddress,
                         city=self.city,
                         state=self.state,
                         zip=self.zip,
                         description=self.description,
                         instructions=self.instructions,
                         country=self.country,
                         eventType=self.eventType,
                         zoomRequired=self.zoomRequired)


# One background publish run (tools/tasks.py publishEventJob). The row carries
# the serialized form input out to the Huey worker and the outcome back to the
# polling status page - it is the ONLY source of truth for the run (Huey's
# result store is off). PostedEvents stays the truth of what was published;
# this is the truth of what was attempted.
class PublishJob(models.Model):
    class Status:
        PENDING = 0
        RUNNING = 1
        PUBLISHED = 2
        # A gCal conflict the user may force past (direct flow only)
        CONFLICT = 3
        # A Zoom conflict - no free account, nothing to force
        UNRESOLVEABLE = 4
        FAILED = 5

    TERMINAL_STATUSES = (Status.PUBLISHED, Status.CONFLICT, Status.UNRESOLVEABLE, Status.FAILED)

    class Kind:
        DIRECT = 0
        DELEGATED = 1

    # Schema version stamped into every payload; publishEventJob refuses any
    # other version so a future schema change fails loudly instead of
    # publishing garbage from a stale queued job.
    # v2 (issue #26): startIso/endIso changed from tz-aware local ISO to the
    # literal local WALL time (naive ISO), with the zone carried in "timezone"
    # and reapplied on rehydrate. Any v1 job still queued is rejected rather
    # than misread.
    PAYLOAD_VERSION = 2

    kind = models.IntegerField()
    status = models.IntegerField(default=Status.PENDING)
    # The serialized EventInfo + flags (see eventViews._buildEventPayload)
    payload = models.JSONField()
    # Conflict dicts written by the task (see tasks._serializeConflicts);
    # datetimes stored as NAIVE ISO strings already localized to the payload
    # timezone, mirroring the localize-then-strip the views used to do inline.
    conflicts = models.JSONField(default=list, blank=True)
    errorMessage = models.TextField(blank=True)

    creator = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="publishJobsCreated")
    owner = models.ForeignKey(EventOwners, on_delete=models.SET_NULL, blank=True, null=True, related_name="publishJobs")
    postedEvent = models.ForeignKey(PostedEvents, on_delete=models.SET_NULL, blank=True, null=True, related_name="publishJobs")
    delegatedEvent = models.ForeignKey(DelegatedEvents, on_delete=models.SET_NULL, blank=True, null=True, related_name="publishJobs")

    createdAt = models.DateTimeField(auto_now_add=True)
    startedAt = models.DateTimeField(null=True, blank=True, default=None)
    finishedAt = models.DateTimeField(null=True, blank=True, default=None)

    def getStatusAsString(self) -> str:
        if self.status == PublishJob.Status.PENDING:
            return "Pending"
        elif self.status == PublishJob.Status.RUNNING:
            return "Running"
        elif self.status == PublishJob.Status.PUBLISHED:
            return "Published"
        elif self.status == PublishJob.Status.CONFLICT:
            return "Conflict"
        elif self.status == PublishJob.Status.UNRESOLVEABLE:
            return "Unresolveable Conflict"
        elif self.status == PublishJob.Status.FAILED:
            return "Failed"
        else:
            return f"Unknown {self.status}"

    def getKindAsString(self) -> str:
        if self.kind == PublishJob.Kind.DIRECT:
            return "Direct"
        elif self.kind == PublishJob.Kind.DELEGATED:
            return "Delegated"
        else:
            return f"Unknown {self.kind}"

    def isTerminal(self) -> bool:
        return self.status in PublishJob.TERMINAL_STATUSES

    def getStatusUrl(self) -> str:
        return reverse("publish-status", kwargs={"jobId": self.id})

    def getResultContext(self) -> dict:
        """The template context for this job's terminal status - a thin,
        single-purpose status->context map (the QRCode.resolveTarget rationale:
        one source of truth the view and tests both consume; split it if it
        grows). The view picks the template per (status x kind); this only
        shapes the data those existing templates already expect."""
        if self.status == PublishJob.Status.PUBLISHED:
            event = self.postedEvent
            return {
                "anShareLink": event.anShareLink if event else "",
                "anManageLink": event.anManageLink if event else "",
                "gCalLink": event.gCalLink if event else "",
                "zoomLink": event.zoomLink if event else "",
                "zoomAccount": event.zoomAccount if event else "",
            }
        if self.status in (PublishJob.Status.CONFLICT, PublishJob.Status.UNRESOLVEABLE):
            # Reconstruct naive datetimes so conflictList.html renders through
            # Django's default formatting exactly as the inline views did.
            return {"conflicts": [
                {
                    "type": conflict["type"],
                    "title": conflict["title"],
                    "zoomUser": conflict["zoomUser"],
                    "start": datetime.datetime.fromisoformat(conflict["startIso"]),
                    "end": datetime.datetime.fromisoformat(conflict["endIso"]),
                }
                for conflict in self.conflicts
            ]}
        if self.status == PublishJob.Status.FAILED:
            return {"errorStr": self.errorMessage}
        return {}


# A member's request to join an event owner (committee), be added to a group,
# or be granted one of the custom tools.* permissions. Mirrors the
# DelegatedEvents request/approve pattern: the row is the request/audit record,
# approvers are reached by an emailed review link, and the actual grant happens
# on approval.
class AccessRequests(models.Model):
    class Status:
        REQUESTED = 0
        DENIED = 1
        APPROVED = 2

    requester = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="accessRequestsCreated",
    )

    # Exactly one of group / permission / owner is set - see clean().
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="accessRequests",
    )
    permission = models.ForeignKey(
        Permission, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="accessRequests",
    )
    # Applying to join an event owner (committee): approval adds the requester
    # to owner.authorizers, and the owner's current authorizers are the peer
    # reviewers (see canBeReviewedBy). SET_NULL so an owner deleted in /admin/
    # leaves reviewed history intact, exactly like the group/permission targets.
    owner = models.ForeignKey(
        EventOwners, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="accessRequests",
    )

    justification = models.TextField(
        help_text="Why the requester needs this access, e.g. 'I run events for the Anti-ICE campaign.'",
    )

    status = models.IntegerField(default=Status.REQUESTED)
    reviewer = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True,
        related_name="accessRequestsReviewed",
    )
    reason = models.TextField(blank=True)

    dateCreated = models.DateTimeField(auto_now_add=True)
    dateReviewed = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        verbose_name = "Access Request"

    def __str__(self) -> str:
        return f"{self.getRequesterName()} -> {self.getTargetDescription()} ({self.getStatusAsString()})"

    def clean(self):
        from django.core.exceptions import ValidationError

        targets = [
            self.group_id is not None,
            self.permission_id is not None,
            self.owner_id is not None,
        ]
        if sum(1 for t in targets if t) != 1:
            raise ValidationError(
                "An access request must target exactly one thing: a group, a permission, or an event owner."
            )

    def getStatusAsString(self) -> str:
        if self.status == AccessRequests.Status.REQUESTED:
            return "Requested"
        elif self.status == AccessRequests.Status.DENIED:
            return "Denied"
        elif self.status == AccessRequests.Status.APPROVED:
            return "Approved"
        else:
            return f"Unkown {self.status}"

    def getRequesterName(self) -> str:
        if self.requester is None:
            return ""
        return self.requester.getUserNameString()

    def getReviewerName(self) -> str:
        if self.reviewer is None:
            return ""
        return self.reviewer.getUserNameString()

    def getTargetDescription(self) -> str:
        if self.group is not None:
            return f"Group: {self.group.name}"
        if self.permission is not None:
            return f"Permission: {self.permission.name}"
        if self.owner is not None:
            return f"Event Owner: {self.owner.name}"
        return "Unknown target"

    def getUrl(self) -> str:
        return reverse("review-access-request", kwargs={"id": self.id})

    def getDateCreatedStr(self) -> str:
        """Rendered in the request's active timezone (the browser's, via
        TimezoneMiddleware) rather than raw UTC."""
        if not self.dateCreated:
            return ""
        return djangoTimezone.localtime(self.dateCreated).strftime(utils.DATE_TIME_FORMAT)

    def canBeReviewedBy(self, user) -> bool:
        """Approver rules: admins (superuser or approveAccessRequest holders)
        can review anything; existing members of the requested group - or
        existing authorizers of the requested event owner - can review requests
        for that group/owner; nobody reviews their own request."""
        if not user.is_authenticated or not user.is_active:
            return False
        if self.requester is not None and user.id == self.requester_id:
            return False
        if user.is_superuser or user.has_perm(permissions.APPROVE_ACCESS_REQUEST):
            return True
        if self.group_id is not None:
            return user.groups.filter(id=self.group_id).exists()
        if self.owner_id is not None:
            return self.owner.authorizers.filter(id=user.id).exists()
        return False

    def grantTo(self, requester) -> None:
        """Apply the requested access to the requester."""
        if self.group is not None:
            requester.groups.add(self.group)
        elif self.permission is not None:
            requester.user_permissions.add(self.permission)
        elif self.owner is not None:
            # Two deliberately separate steps (Garrigan's bundle-now-separable-later
            # call): (1) committee membership scopes which owner the member may act
            # for; (2) the event-lead role group confers the actual page-level
            # capability. Authorizer membership alone is inert without (2). To stop
            # bundling, drop step (2) or swap in a narrower role group.
            self.owner.authorizers.add(requester)        # (1) committee membership
            self._grantEventLeadRole(requester)          # (2) event-lead capability

    def _grantEventLeadRole(self, requester) -> None:
        """Add the requester to the managed event-lead role group, creating it
        with its permission bundle on first use. Kept distinct from the authorizer
        grant in grantTo so the two can be separated later (see EVENT_LEAD_ROLE_GROUP)."""
        roleGroup, created = Group.objects.get_or_create(name=EVENT_LEAD_ROLE_GROUP)
        if created:
            roleGroup.permissions.add(*Permission.objects.filter(
                codename__in=[name.split(".")[1] for name in EVENT_LEAD_PERMISSIONS],
                content_type__app_label="tools",
            ))
        requester.groups.add(roleGroup)


# ---------------------------------------------------------------------------
# Link Tree
#
# A self-hosted replacement for the chapter's third-party "linktree" pages. A
# LinkTree is a public (or members-only) page of LinkTreeItems. Every outbound
# click and every QR scan is routed through the site (see linkTreeViews.go /
# qr_redirect) so usage is tracked and a printed QR code stays repointable.
#
# Items can be plain MANUAL links or WIKI links that surface Outline content
# (e.g. "the latest GBM agenda"). WIKI items are resolved out-of-band by the
# `sync_link_tree_wiki` management command, which writes the resolved url/label
# onto the item; the public page only ever reads that cache, so it never depends
# on Outline being reachable at request time.
# ---------------------------------------------------------------------------


class LinkTree(models.Model):
    class Visibility:
        PUBLIC = 0   # anyone, no login
        MEMBERS = 1  # login required

    VISIBILITY_CHOICES = (
        (Visibility.PUBLIC, "Public - anyone with the link"),
        (Visibility.MEMBERS, "Members only - requires login"),
    )

    slug = models.SlugField(
        max_length=80, unique=True,
        help_text="Used in the public URL, e.g. 'links' → /t/links/. Lowercase, no spaces.",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(
        blank=True, help_text="Optional blurb shown under the title on the public page.",
    )
    visibility = models.IntegerField(choices=VISIBILITY_CHOICES, default=Visibility.PUBLIC)
    isActive = models.BooleanField(
        default=True, help_text="Uncheck to take the whole tree offline (returns 404).",
    )
    # Optional per-committee scoping, mirroring the EventOwners.authorizers
    # pattern used for events. Not enforced in v1 (the manageLinkTree permission
    # gates editing globally); reserved for future per-owner edit scoping.
    owner = models.ForeignKey(
        EventOwners, on_delete=models.SET_NULL, blank=True, null=True, related_name="linkTrees",
    )

    dateCreated = models.DateTimeField(auto_now_add=True)
    dateModified = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Link Tree"

    def __str__(self) -> str:
        return f"{self.title} (/t/{self.slug}/)"

    def getPublicUrl(self) -> str:
        return reverse("link-tree", kwargs={"slug": self.slug})

    def isMembersOnly(self) -> bool:
        return self.visibility == LinkTree.Visibility.MEMBERS

    def activeItems(self):
        """Active items in display order, honoring any visibility window.

        Filters the (prefetched) related set in Python so a single prefetch on
        the public view covers ordering and the time-window logic together.
        """
        now = datetime.datetime.now(datetime.UTC)
        visible = []
        for item in self.items.all():
            if not item.isActive:
                continue
            if item.visibleFrom is not None and now < item.visibleFrom:
                continue
            if item.visibleUntil is not None and now > item.visibleUntil:
                continue
            visible.append(item)
        return visible


class LinkTreeItem(models.Model):
    class Kind:
        MANUAL = 0          # a fixed url the maintainer types in
        WIKI = 1            # resolved from the Outline wiki by sync_link_tree_wiki
        SECTION_HEADER = 2  # a non-clickable heading that groups the items below it

    KIND_CHOICES = (
        (Kind.MANUAL, "Manual link"),
        (Kind.WIKI, "Wiki link (auto-surfaced from Outline)"),
        (Kind.SECTION_HEADER, "Section header (not a link)"),
    )

    class WikiMode:
        LATEST_MATCH = 0  # newest published doc whose title matches wikiQuery
        PINNED = 1        # one specific document, by id

    WIKI_MODE_CHOICES = (
        (WikiMode.LATEST_MATCH, "Latest matching document"),
        (WikiMode.PINNED, "Pinned document"),
    )

    tree = models.ForeignKey(LinkTree, on_delete=models.CASCADE, related_name="items")
    order = models.PositiveIntegerField(
        default=0, help_text="Lower numbers appear first.",
    )
    kind = models.IntegerField(
        choices=KIND_CHOICES, default=Kind.MANUAL,
        help_text="Manual link (you type the URL), wiki link (auto-pulled from Outline), "
        "or a section header (a non-clickable heading that groups the items below it).",
    )

    label = models.CharField(
        max_length=200, blank=True,
        help_text="Button text - or the heading text for a section header. For wiki "
        "links, leave blank to use the document's own title.",
    )
    subtitle = models.CharField(
        max_length=300, blank=True,
        help_text="Optional smaller line shown under the label.",
    )
    icon = models.CharField(
        max_length=8, blank=True,
        help_text="Optional emoji shown before the label, e.g. 📅 or 🗳️.",
    )
    isActive = models.BooleanField(
        default=True,
        help_text="Uncheck to hide this item from the page without deleting it.",
    )

    # Optional show/hide window (great for event-specific links). UTC in DB.
    visibleFrom = models.DateTimeField(
        null=True, blank=True,
        help_text="Optional: don't show the item before this time (UTC).",
    )
    visibleUntil = models.DateTimeField(
        null=True, blank=True,
        help_text="Optional: stop showing the item after this time (UTC).",
    )

    # --- MANUAL ---
    url = models.URLField(
        max_length=2000, blank=True,
        help_text="Destination URL. Used for manual links (ignored for wiki links and headers).",
    )

    # --- WIKI ---
    wikiMode = models.IntegerField(
        choices=WIKI_MODE_CHOICES, default=WikiMode.LATEST_MATCH,
        help_text="For wiki links: surface the newest document matching the query, "
        "or always link one specific pinned document.",
    )
    wikiQuery = models.CharField(
        max_length=200, blank=True,
        help_text="For 'latest matching': title text to search, e.g. 'GBM Agenda'.",
    )
    wikiCollectionId = models.CharField(
        max_length=100, blank=True,
        help_text="Optional Outline collection id to scope the search.",
    )
    pinnedWikiDocId = models.CharField(
        max_length=100, blank=True, help_text="For 'pinned': the Outline document id.",
    )

    # Cache written by sync_link_tree_wiki; read by the public page.
    resolvedUrl = models.TextField(
        blank=True,
        help_text="Auto-filled for wiki links by the sync command - the resolved document URL.",
    )
    resolvedLabel = models.CharField(
        max_length=300, blank=True,
        help_text="Auto-filled for wiki links by the sync command - the resolved document title.",
    )
    resolvedAt = models.DateTimeField(
        null=True, blank=True,
        help_text="When the wiki link was last resolved by the sync command.",
    )

    class Meta:
        verbose_name = "Link Tree Item"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.displayLabel() or f"Item {self.pk}"

    def isWiki(self) -> bool:
        return self.kind == LinkTreeItem.Kind.WIKI

    def isHeader(self) -> bool:
        return self.kind == LinkTreeItem.Kind.SECTION_HEADER

    def shouldDisplay(self) -> bool:
        """A header always shows; a link shows only once it has a destination."""
        return self.isHeader() or self.isResolved()

    def displayLabel(self) -> str:
        """What to show on the button - explicit label wins, else resolved title."""
        return self.label or (self.resolvedLabel if self.isWiki() else "")

    def destinationUrl(self) -> str | None:
        """The real URL to redirect to. None if a wiki item hasn't resolved yet."""
        if self.isWiki():
            return self.resolvedUrl or None
        return self.url or None

    def isResolved(self) -> bool:
        return self.destinationUrl() is not None

    def trackedUrl(self) -> str | None:
        """Site URL that logs a click then redirects (what the page links to).

        None for a section header - a header is not a link, so it has no tracked
        destination. Callers (and the template) gate on isHeader() before using
        this, and this makes that contract honest rather than relying on the
        template alone.
        """
        if self.isHeader():
            return None
        return reverse("link-go", kwargs={"item_id": self.pk})


class QRCode(models.Model):
    """A repointable, tracked QR code.

    The generated image encodes the site's /qr/<code>/ URL - NOT the destination.
    Scans hit qr_redirect, which logs the scan and 302s to the current target, so
    a printed code can be repointed in admin without reprinting and every scan is
    still counted. Exactly one of tree / item / rawUrl is the target.
    """

    code = models.SlugField(
        max_length=40, unique=True,
        help_text="Short token in the QR URL, e.g. 'spring-tabling' → /qr/spring-tabling/.",
    )
    label = models.CharField(
        max_length=200, help_text="Human label, e.g. 'Spring 2026 tabling flyer'.",
    )
    campaign = models.CharField(
        max_length=100, blank=True,
        help_text="Optional medium/source tag to break down scans, e.g. 'flyer' or 'table-tent'.",
    )

    tree = models.ForeignKey(
        LinkTree, on_delete=models.SET_NULL, blank=True, null=True, related_name="qrCodes",
    )
    item = models.ForeignKey(
        LinkTreeItem, on_delete=models.SET_NULL, blank=True, null=True, related_name="qrCodes",
    )
    rawUrl = models.URLField(
        max_length=2000, blank=True, help_text="Target an arbitrary URL instead of a tree/item.",
    )

    isActive = models.BooleanField(default=True)
    createdBy = models.ForeignKey(
        User, on_delete=models.SET_NULL, blank=True, null=True, related_name="qrCodesCreated",
    )
    dateCreated = models.DateTimeField(auto_now_add=True)
    dateModified = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "QR Code"

    def __str__(self) -> str:
        return f"{self.label} (/qr/{self.code}/)"

    def clean(self):
        # Enforce exactly one target so a scan is never ambiguous. Runs in admin
        # (via ModelForm validation) and on any full_clean() call.
        from django.core.exceptions import ValidationError

        targets = [self.tree_id is not None, self.item_id is not None, bool(self.rawUrl)]
        chosen = sum(1 for t in targets if t)
        if chosen != 1:
            raise ValidationError(
                "A QR code must point at exactly one target: a link tree, a link tree item, or a raw URL."
            )

    def scanUrl(self) -> str:
        """Site URL the QR image encodes; logs a scan then redirects."""
        return reverse("qr-redirect", kwargs={"code": self.code})

    def resolveTarget(self):
        """The single source of truth for a QR code's target taxonomy.

        Returns ``(destinationUrl, tree, item)`` where destinationUrl is where a
        scan should 302 (or None if not yet resolvable), and tree/item are the
        objects to attribute the scan to in analytics. Centralizing this here
        means a new target type is added in exactly one place - the view and any
        other caller just consume the tuple.
        """
        if self.tree is not None:
            return self.tree.getPublicUrl(), self.tree, None
        if self.item is not None:
            return self.item.destinationUrl(), self.item.tree, self.item
        return (self.rawUrl or None), None, None

    def targetUrl(self) -> str | None:
        """Just the redirect destination (tree page > item dest > raw url)."""
        return self.resolveTarget()[0]


class LinkEvent(models.Model):
    """Append-only, privacy-first click/scan log.

    Deliberately stores NO raw IP: visitorHash is a salted, daily-rotating digest
    of IP+user-agent, good only for rough same-day unique counts and useless as a
    cross-day identifier. destinationUrl is snapshotted so analytics survive later
    edits to the item/QR target.
    """

    class Source:
        WEB = 0  # a click on the public tree page
        QR = 1   # a QR scan

    SOURCE_CHOICES = (
        (Source.WEB, "Web click"),
        (Source.QR, "QR scan"),
    )

    tree = models.ForeignKey(
        LinkTree, on_delete=models.SET_NULL, blank=True, null=True, related_name="events",
    )
    item = models.ForeignKey(
        LinkTreeItem, on_delete=models.SET_NULL, blank=True, null=True, related_name="events",
    )
    qr = models.ForeignKey(
        QRCode, on_delete=models.SET_NULL, blank=True, null=True, related_name="events",
    )

    source = models.IntegerField(choices=SOURCE_CHOICES)
    occurredAt = models.DateTimeField(auto_now_add=True, db_index=True)

    destinationUrl = models.TextField(blank=True)
    visitorHash = models.CharField(max_length=16, blank=True)
    uaFamily = models.CharField(max_length=40, blank=True)
    referrerHost = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Link Event"
        indexes = [
            models.Index(fields=["tree", "occurredAt"]),
            models.Index(fields=["item", "occurredAt"]),
            models.Index(fields=["qr", "occurredAt"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_source_display()} @ {self.occurredAt:%Y-%m-%d %H:%M}"
