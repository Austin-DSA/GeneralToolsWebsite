import typing
import pytz
import logging
import datetime
import copy

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from .EventAutomation import EventAutomationDriver, ActionNetworkAutomation

from . import permissions
from .models import User, EventOwners, AccessRequests, LinkTree, LinkTreeItem, QRCode, Resolution, PostedEvents

STATES = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
]

class EventTypes:
    TYPES = [
        (ActionNetworkAutomation.ANTypes.IN_PERSON, "In Person"),
        (ActionNetworkAutomation.ANTypes.VIRTUAL, "Virtual"),
        (ActionNetworkAutomation.ANTypes.HYBRID,"Hybrid")
    ]

# The chapter's timezone. Owner expirations are entered and displayed in this
# zone (the DB stores UTC, per the localize-at-the-edges discipline).
CHAPTER_TIMEZONE = "America/Chicago"


def _activeOwnerQueryset():
    """Owners that can actually receive events: active (permanent or unexpired)
    AND with at least one authorizer. This is the single definition of a
    "healthy" owner for form dropdowns; ownerViews.py recomputes the same
    predicate per-owner for its health badges - keep the two in sync.

    Called from form __init__, never at module level: the ``now`` comparison
    must be evaluated per-request, not frozen at import.
    """
    now = datetime.datetime.now(datetime.UTC)
    return (EventOwners.objects
            .annotate(ownerAuthorizerCount=Count("authorizers"))
            .filter(ownerAuthorizerCount__gt=0)
            .filter(Q(isPermanent=True) | Q(expiration__gt=now)))


class NewEventForm(forms.Form):
    class Keys:
        TITLE = "title"
        DESCRIPTION = "description"
        EVENT_TYPE = "eventType"
        # START_DATE = "startDate"
        START_TIME = "startTime"
        # END_DATE = "endDate"
        END_TIME = "endTime"
        TIMEZONE = "timezone"
        INSTRUCTIONS = "instructions"
        LOCATION_NAME = "locationName"
        ADDRESS = "address"
        CITY = "city"
        STATE = "state"
        COUNTRY = "country"
        ZIP_CODE = "zipcode"
        OWNER = "owner"
        IGNORE_RESOLVEABLE_CONFLICTS = "ignoreResolveableConflics"
        # ZOOM_REQUIRED = "zoomRequired"

    # Restricted to healthy owners in __init__ (see _activeOwnerQueryset). This
    # is a selection filter, not the security gate - the views' isActive() and
    # authorizer-membership checks remain authoritative. A crafted POST naming
    # an unhealthy owner fails validation here as an invalid choice.
    owner = forms.ModelChoiceField(
        label="Event Owner",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        to_field_name="name",
        queryset=EventOwners.objects.none()
    )
    title = forms.CharField(
        label="Event title",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    description = forms.CharField(
        label="Description",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    eventType = forms.TypedChoiceField(
        label="Event Type",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices=EventTypes.TYPES,
        coerce=int,
        empty_value=0
    )
    timezone = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={timezone: timezone for timezone in ActionNetworkAutomation.TimeZone.TZ_TO_AN_TZ.keys()},
        initial="America/Chicago",
    )
    # type="datetime-local" gives the native browser date/time picker; its
    # value format is fixed as YYYY-MM-DDTHH:MM, hence the explicit
    # widget format + input_formats.
    startTime = forms.DateTimeField(
        label="Start time",
        widget=forms.DateTimeInput(
            attrs={"class": "form-field w-full", "type": "datetime-local", "step": "60"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
    )
    endTime = forms.DateTimeField(
        label="End time",
        widget=forms.DateTimeInput(
            attrs={"class": "form-field w-full", "type": "datetime-local", "step": "60"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
    )
    instructions = forms.CharField(
        label="Instructions",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    locationName = forms.CharField(
        label="Location name",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}), required=False
    )
    address = forms.CharField(
        label="Address", widget=forms.TextInput(attrs={"class": "form-field w-full"}), required=False
    )
    city = forms.CharField(
        label="City",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        initial="Austin",
        required=False
    )
    choices = {state: state for state in STATES}
    state = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices=choices,
        initial="TX",
        required=False
    )
    country = forms.CharField(
        label="Country",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        initial="US",
        required=False,
    )
    zipcode = forms.IntegerField(
        label="Zip code", 
        widget=forms.NumberInput(attrs={"class": "form-field w-full"}),
        required=False,
    )
    ignoreResolveableConflics = forms.BooleanField(
        label="Publish even if the calendar is busy",
        help_text="Normally we stop if another event already overlaps this time on Google Calendar. "
        "Check this to publish anyway. (A Zoom conflict can never be overridden - there has to be a free Zoom account.)",
        widget=forms.CheckboxInput(),
        required=False,
    )
    # zoomRequired = forms.BooleanField(
    #     label="Zoom Meeting Required",
    #     widget=forms.CheckboxInput(),
    #     required=False,
    #     initial=True
    # )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields[NewEventForm.Keys.OWNER].queryset = _activeOwnerQueryset()

    def clean_zipcode(self):
        data = self.cleaned_data[NewEventForm.Keys.ZIP_CODE]
        # Zip code is not always necessary
        if data is None:
            return ""
        zip_str = str(data)

        if len(zip_str) == 5:
            return data

        else:
            raise ValidationError(_("Zip code must be five digits long"))

    def convertToEventInfo(self) -> EventAutomationDriver.EventInfo | None:
        if not self.is_valid():
            return None
        formData = self.cleaned_data
        timezoneStr = formData[NewEventForm.Keys.TIMEZONE]
        timezone = pytz.timezone(timezoneStr)
        start: datetime.datetime = formData[NewEventForm.Keys.START_TIME]
        end: datetime.datetime = formData[NewEventForm.Keys.END_TIME]
        # The start and end dates in the form appear to assume UTC time zone
        # We need to force localize to the input timezone
        # BTW I hate timezones
        if start.tzinfo is None or start.tzinfo.utcoffset(start) is None:
            start = timezone.localize(start)
        else:
            start = start.replace(tzinfo=None)
            start = timezone.localize(start)
        if end.tzinfo is None or end.tzinfo.utcoffset(end) is None:
            end = timezone.localize(end)
        else:
            end = end.replace(tzinfo=None)
            end = timezone.localize(end)
        eventType = formData[NewEventForm.Keys.EVENT_TYPE]
        zoomRequired = eventType in [ActionNetworkAutomation.ANTypes.HYBRID, ActionNetworkAutomation.ANTypes.VIRTUAL]
        eventInfo = EventAutomationDriver.EventInfo(
            title=formData[NewEventForm.Keys.TITLE],
            start=start,
            end=end,
            locationName=formData[NewEventForm.Keys.LOCATION_NAME],
            streetAddress=formData[NewEventForm.Keys.ADDRESS],
            city=formData[NewEventForm.Keys.CITY],
            state=formData[NewEventForm.Keys.STATE],
            zip=formData[NewEventForm.Keys.ZIP_CODE],
            description=formData[NewEventForm.Keys.DESCRIPTION],
            instructions=formData[NewEventForm.Keys.INSTRUCTIONS],
            country=formData[NewEventForm.Keys.COUNTRY],
            zoomRequired=zoomRequired,
            eventType=eventType
        )
        return eventInfo
    
class ApproveDelegatedEventForm(forms.Form):
    class Keys:
        APPROVE = "approve"
        REASON = "reason"
    approve = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={x: x for x in ["YES", "NO"]},
        initial="YES",
    )
    reason = forms.CharField(
        label="Reason (optional)",
        widget=forms.Textarea(attrs={"rows": "3", "class": "form-field w-full"}),
        required=False,
    )


class RegisterForm(UserCreationForm):
    """Self-service account creation. New accounts are active immediately but
    carry no permissions - everything useful is granted later via groups or an
    access request."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Approval emails and getUserNameString() depend on these.
        for name in ("first_name", "last_name", "email"):
            self.fields[name].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-field w-full")
        # Autocomplete hints so browser password managers treat this as a
        # proper sign-up form (offer to generate + save the password).
        # UserCreationForm already sets autocomplete="new-password" on both
        # password fields and "username" on username.
        for name, token in (
            ("email", "email"),
            ("first_name", "given-name"),
            ("last_name", "family-name"),
        ):
            self.fields[name].widget.attrs.setdefault("autocomplete", token)
        # Mirror MinimumLengthValidator for native browser validation
        self.fields["password1"].widget.attrs.setdefault("minlength", "8")

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email address already exists.")
        return email


class AccessRequestForm(forms.Form):
    class Keys:
        TARGET = "target"
        JUSTIFICATION = "justification"

    OWNER_PREFIX = "o"
    PERMISSION_PREFIX = "p"

    target = forms.ChoiceField(
        label="What access do you need?",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    justification = forms.CharField(
        label="Why do you need it?",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
        min_length=1,
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Members apply to join an event owner (committee) - approval adds them
        # to owner.authorizers, and the owner's current authorizers become the
        # peer reviewers (mirrors the old group-peer rule). Only owners that can
        # still receive events are offered (permanent or unexpired); an expired
        # owner can't approve anything. RBAC groups are assigned by an admin via
        # Manage Access, not self-requested here.
        now = datetime.datetime.now(datetime.UTC)
        activeOwners = (
            EventOwners.objects
            .filter(Q(isPermanent=True) | Q(expiration__gt=now))
            .order_by("name")
        )
        ownerChoices = [
            (f"{self.OWNER_PREFIX}:{owner.id}", owner.name)
            for owner in activeOwners
        ]
        permissionChoices = [
            (f"{self.PERMISSION_PREFIX}:{permission.id}", permission.name)
            for permission in permissions.getRequestablePermissions()
        ]
        self.fields[self.Keys.TARGET].choices = [
            ("Event Owners", ownerChoices),
            ("Permissions", permissionChoices),
        ]

    def clean(self):
        cleanedData = super().clean()
        targetValue = cleanedData.get(self.Keys.TARGET)
        if not targetValue:
            return cleanedData
        kind, _, targetId = targetValue.partition(":")
        owner = None
        permission = None
        # The ChoiceField already validated the value against the rendered
        # choices, but the target may have been deleted since the form loaded
        if kind == self.OWNER_PREFIX:
            owner = EventOwners.objects.filter(id=targetId).first()
            if owner is None:
                raise ValidationError("The selected option is no longer available.")
            if owner.authorizers.filter(id=self.user.id).exists():
                raise ValidationError(f"You can already publish events for {owner.name}.")
            alreadyPending = AccessRequests.objects.filter(
                requester=self.user, owner=owner, status=AccessRequests.Status.REQUESTED
            ).exists()
        else:
            permission = Permission.objects.filter(id=targetId).first()
            if permission is None:
                raise ValidationError("The selected option is no longer available.")
            if self.user.has_perm("tools." + permission.codename):
                raise ValidationError(f"You already have the permission {permission.name}.")
            alreadyPending = AccessRequests.objects.filter(
                requester=self.user, permission=permission, status=AccessRequests.Status.REQUESTED
            ).exists()
        if alreadyPending:
            raise ValidationError("You already have a pending request for this access.")
        # group is no longer self-requestable here, but the view reads all three
        # keys uniformly when creating the row - keep it present and null.
        cleanedData["group"] = None
        cleanedData["owner"] = owner
        cleanedData["permission"] = permission
        return cleanedData


class ReviewAccessRequestForm(forms.Form):
    class Keys:
        APPROVE = "approve"
        REASON = "reason"

    approve = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={x: x for x in ["YES", "NO"]},
        initial="YES",
    )
    reason = forms.CharField(
        label="Reason (optional)",
        widget=forms.Textarea(attrs={"rows": "3", "class": "form-field w-full"}),
        required=False,
    )


class _PermissionMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class ManageAccessForm(forms.Form):
    """Direct grant/revoke of a member's groups and custom permissions
    (the admin-side counterpart to the request/approve flow)."""

    class Keys:
        GROUPS = "groups"
        PERMISSIONS = "permissions"

    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Groups",
    )
    permissions = _PermissionMultipleChoiceField(
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Directly-granted permissions",
        help_text="Permissions the member holds individually, on top of whatever their groups grant.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields[self.Keys.PERMISSIONS].queryset = permissions.getRequestablePermissions()


class GroupForm(forms.Form):
    """Create/edit a group on the front-end Manage Groups pages.

    The list page renders only the name field (create); the detail page
    renders all three. Like ManageAccessForm, the checkboxes are rendered
    manually in the template - this form just parses them back."""

    class Keys:
        NAME = "name"
        PERMISSIONS = "permissions"
        ADD_MEMBERS = "addMembers"
        REMOVE_MEMBERS = "removeMembers"

    name = forms.CharField(
        max_length=150,
        label="Group name",
        widget=forms.TextInput(attrs={"class": "form-field"}),
    )
    permissions = _PermissionMultipleChoiceField(
        queryset=Permission.objects.none(),
        required=False,
        label="Permissions this group grants",
    )
    # Membership is submitted as deltas, not the full set - the page only ever
    # names the members it's changing, so a stale tab can't wipe a roster and
    # the form scales past orgs too big to render as checkboxes.
    addMembers = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
    )
    removeMembers = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
    )

    def __init__(self, *args, group: Group | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group = group
        self.fields[self.Keys.PERMISSIONS].queryset = permissions.getRequestablePermissions()
        activeUsers = User.objects.filter(is_active=True).order_by("username")
        self.fields[self.Keys.ADD_MEMBERS].queryset = activeUsers
        self.fields[self.Keys.REMOVE_MEMBERS].queryset = activeUsers

    def clean_name(self):
        name = self.cleaned_data[self.Keys.NAME].strip()
        existing = Group.objects.filter(name__iexact=name)
        if self._group is not None:
            existing = existing.exclude(id=self._group.id)
        if existing.exists():
            raise ValidationError("A group with that name already exists.")
        return name

    def clean(self):
        cleaned = super().clean()
        adds = set(cleaned.get(self.Keys.ADD_MEMBERS) or [])
        removes = set(cleaned.get(self.Keys.REMOVE_MEMBERS) or [])
        if adds & removes:
            raise ValidationError("A member can't be both added and removed in the same save.")
        return cleaned


class EventOwnerForm(forms.Form):
    """Create/edit an event owner on the front-end Manage Event Owners pages.

    Like GroupForm, authorizer membership is submitted as deltas
    (addAuthorizers / removeAuthorizers hidden inputs staged by the page's
    typeahead JS), so a stale tab can't wipe a roster. Pass ``owner`` for edit
    mode; create mode leaves it None."""

    class Keys:
        NAME = "ownerName"
        EXPIRATION = "ownerExpiration"
        IS_PERMANENT = "ownerIsPermanent"
        ADD_AUTHORIZERS = "addAuthorizers"
        REMOVE_AUTHORIZERS = "removeAuthorizers"

    ownerName = forms.CharField(
        max_length=100,
        label="Owner name",
        widget=forms.TextInput(attrs={"class": "form-field"}),
    )
    # Same datetime-local widget contract as NewEventForm.startTime above.
    ownerExpiration = forms.DateTimeField(
        label="Expires on (Central Time)",
        required=False,
        widget=forms.DateTimeInput(
            attrs={"class": "form-field w-full", "type": "datetime-local", "step": "60"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text="Ignored while Permanent is on. To deactivate an owner, set a date in the past.",
    )
    ownerIsPermanent = forms.BooleanField(
        label="Permanent (never expires)",
        required=False,
        widget=forms.CheckboxInput(),
    )
    addAuthorizers = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
    )
    removeAuthorizers = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
    )

    def __init__(self, *args, owner: EventOwners | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._owner = owner
        activeUsers = User.objects.filter(is_active=True).order_by("username")
        self.fields[self.Keys.ADD_AUTHORIZERS].queryset = activeUsers
        self.fields[self.Keys.REMOVE_AUTHORIZERS].queryset = activeUsers

    def clean_ownerName(self):
        name = self.cleaned_data[self.Keys.NAME].strip()
        existing = EventOwners.objects.filter(name__iexact=name)
        if self._owner is not None:
            existing = existing.exclude(id=self._owner.id)
        if existing.exists():
            raise ValidationError("An event owner with that name already exists.")
        return name

    def clean(self):
        cleaned = super().clean()
        adds = set(cleaned.get(self.Keys.ADD_AUTHORIZERS) or [])
        removes = set(cleaned.get(self.Keys.REMOVE_AUTHORIZERS) or [])
        if adds & removes:
            raise ValidationError("An authorizer can't be both added and removed in the same save.")

        isPermanent = cleaned.get(self.Keys.IS_PERMANENT, False)
        expiration = cleaned.get(self.Keys.EXPIRATION)
        if expiration is not None:
            # The datetime-local input is naive Central Time, but under
            # USE_TZ Django hands it to us tagged as the current (UTC)
            # timezone - strip that and localize properly, exactly like
            # NewEventForm.convertToEventInfo does for start/end.
            chapterTimezone = pytz.timezone(CHAPTER_TIMEZONE)
            if expiration.tzinfo is not None and expiration.tzinfo.utcoffset(expiration) is not None:
                expiration = expiration.replace(tzinfo=None)
            cleaned[self.Keys.EXPIRATION] = chapterTimezone.localize(expiration).astimezone(pytz.utc)
        elif isPermanent:
            if self._owner is not None:
                # Edit mode: keep whatever expiration is already stored.
                cleaned[self.Keys.EXPIRATION] = self._owner.expiration
            else:
                # Sentinel: only valid alongside isPermanent=True. isActive()
                # short-circuits on isPermanent so the value is never read,
                # but it must never be written without the flag - a
                # non-permanent owner with this expiration would read as
                # active until 2099.
                cleaned[self.Keys.EXPIRATION] = datetime.datetime(2099, 12, 31, 23, 59, tzinfo=datetime.UTC)
        else:
            self.add_error(self.Keys.EXPIRATION, "Set an expiration date or mark the owner as permanent.")
        return cleaned


# --- Link Tree management forms (maintainers only) -------------------------
#
# All three are plain forms.Form subclasses (the established convention in this
# module) with the widget class declared on each field. Form-level validation is
# the sole, authoritative guard: the views assign cleaned values to the model
# instance field-by-field and call .save() - they never call the model's
# full_clean()/clean(), so the uniqueness and one-target checks below are the
# only enforcement points (mirroring GroupForm.clean_name).


class LinkTreeSettingsForm(forms.Form):
    """Create/edit a link tree's settings. owner is deliberately not exposed
    (reserved for future per-owner scoping); the view leaves it untouched."""

    class Keys:
        SLUG = "slug"
        TITLE = "title"
        DESCRIPTION = "description"
        VISIBILITY = "visibility"
        IS_ACTIVE = "isActive"

    slug = forms.SlugField(
        label="Slug",
        help_text="Used in the public URL, e.g. 'links' -> /t/links/. Lowercase, no spaces.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    title = forms.CharField(
        label="Title",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    description = forms.CharField(
        label="Description",
        required=False,
        help_text="Optional blurb shown under the title on the public page.",
        widget=forms.Textarea(attrs={"rows": "4", "class": "form-field w-full"}),
    )
    visibility = forms.TypedChoiceField(
        label="Visibility",
        choices=LinkTree.VISIBILITY_CHOICES,
        coerce=int,
        empty_value=LinkTree.Visibility.PUBLIC,
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    isActive = forms.BooleanField(
        label="Active",
        required=False,
        help_text="Uncheck to take the whole tree offline (returns 404).",
        widget=forms.CheckboxInput(),
    )

    def __init__(self, *args, tree=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tree = tree

    def clean_slug(self):
        slug = self.cleaned_data[self.Keys.SLUG].strip()
        existing = LinkTree.objects.filter(slug=slug)
        if self._tree is not None:
            existing = existing.exclude(pk=self._tree.pk)
        if existing.exists():
            raise ValidationError("A link tree with that slug already exists.")
        return slug


class LinkTreeItemForm(forms.Form):
    """Add/edit a single link tree item. The view never writes the resolve cache
    (resolvedUrl/resolvedLabel/resolvedAt) - wiki resolution is out-of-band, so
    those fields are not exposed here. visibleFrom/visibleUntil round-trip as
    UTC: the naive datetime-local value is read back as UTC, and stored UTC
    values are rendered without localization."""

    class Keys:
        KIND = "kind"
        ORDER = "order"
        ICON = "icon"
        LABEL = "label"
        SUBTITLE = "subtitle"
        URL = "url"
        IS_ACTIVE = "isActive"
        VISIBLE_FROM = "visibleFrom"
        VISIBLE_UNTIL = "visibleUntil"
        WIKI_MODE = "wikiMode"
        WIKI_QUERY = "wikiQuery"
        WIKI_COLLECTION_ID = "wikiCollectionId"
        PINNED_WIKI_DOC_ID = "pinnedWikiDocId"

    kind = forms.TypedChoiceField(
        label="Kind",
        choices=LinkTreeItem.KIND_CHOICES,
        coerce=int,
        empty_value=LinkTreeItem.Kind.MANUAL,
        help_text="Manual link (you type the URL), wiki link (auto-pulled from Outline), "
        "or a section header (a non-clickable heading that groups the items below it).",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    order = forms.IntegerField(
        label="Order",
        min_value=0,
        help_text="Lower numbers appear first.",
        widget=forms.NumberInput(attrs={"class": "form-field w-full"}),
    )
    icon = forms.CharField(
        label="Icon",
        required=False,
        help_text="Optional emoji shown before the label, e.g. a calendar or ballot box.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    label = forms.CharField(
        label="Label",
        required=False,
        help_text="Button text - or the heading text for a section header. For wiki "
        "links, leave blank to use the document's own title.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    subtitle = forms.CharField(
        label="Subtitle",
        required=False,
        help_text="Optional smaller line shown under the label.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    url = forms.URLField(
        label="URL",
        required=False,
        help_text="Destination URL. Used for manual links (ignored for wiki links and headers).",
        widget=forms.URLInput(attrs={"class": "form-field w-full"}),
    )
    isActive = forms.BooleanField(
        label="Active",
        required=False,
        help_text="Uncheck to hide this item from the page without deleting it.",
        widget=forms.CheckboxInput(),
    )
    # type="datetime-local" gives the native browser picker; its value format is
    # fixed as YYYY-MM-DDTHH:MM. The value is treated as UTC (see clean_* below).
    visibleFrom = forms.DateTimeField(
        label="Visible from (UTC)",
        required=False,
        help_text="Optional: don't show the item before this time (UTC).",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "form-field w-full"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
    )
    visibleUntil = forms.DateTimeField(
        label="Visible until (UTC)",
        required=False,
        help_text="Optional: stop showing the item after this time (UTC).",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "form-field w-full"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
    )
    wikiMode = forms.TypedChoiceField(
        label="Wiki mode",
        choices=LinkTreeItem.WIKI_MODE_CHOICES,
        coerce=int,
        empty_value=LinkTreeItem.WikiMode.LATEST_MATCH,
        required=False,
        help_text="For wiki links: surface the newest document matching the query, "
        "or always link one specific pinned document.",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    wikiQuery = forms.CharField(
        label="Wiki query",
        required=False,
        help_text="For 'latest matching': title text to search, e.g. 'GBM Agenda'.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    wikiCollectionId = forms.CharField(
        label="Wiki collection id",
        required=False,
        help_text="Optional Outline collection id to scope the search.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    pinnedWikiDocId = forms.CharField(
        label="Pinned wiki document id",
        required=False,
        help_text="For 'pinned': the Outline document id.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )

    def clean_visibleFrom(self):
        return self._asUtc(self.cleaned_data.get(self.Keys.VISIBLE_FROM))

    def clean_visibleUntil(self):
        return self._asUtc(self.cleaned_data.get(self.Keys.VISIBLE_UNTIL))

    @staticmethod
    def _asUtc(value):
        """Treat the naive datetime-local value as UTC so the stored value is an
        aware UTC datetime (the model documents these fields as UTC-in-DB)."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get(self.Keys.KIND)
        isManualKind = kind == LinkTreeItem.Kind.MANUAL
        isWikiKind = kind == LinkTreeItem.Kind.WIKI
        isSectionHeaderKind = kind == LinkTreeItem.Kind.SECTION_HEADER

        # Clear cross-kind fields that don't apply, so a kind switch never leaves
        # stale data behind (hidden rows still POST their values).
        if isWikiKind or isSectionHeaderKind:
            cleaned[self.Keys.URL] = ""
        if isManualKind or isSectionHeaderKind:
            cleaned[self.Keys.WIKI_QUERY] = ""
            cleaned[self.Keys.WIKI_COLLECTION_ID] = ""
            cleaned[self.Keys.PINNED_WIKI_DOC_ID] = ""

        # Per-kind required fields.
        if isManualKind and not cleaned.get(self.Keys.URL):
            raise ValidationError("A manual link needs a destination URL.")
        if isWikiKind:
            wikiMode = cleaned.get(self.Keys.WIKI_MODE)
            if wikiMode == LinkTreeItem.WikiMode.PINNED:
                if not cleaned.get(self.Keys.PINNED_WIKI_DOC_ID):
                    raise ValidationError("A pinned wiki link needs a pinned document id.")
            elif not cleaned.get(self.Keys.WIKI_QUERY):
                raise ValidationError("A latest-matching wiki link needs a wiki query.")
        if isSectionHeaderKind and not cleaned.get(self.Keys.LABEL):
            raise ValidationError("A section header needs a label.")

        return cleaned


class QRCodeForm(forms.Form):
    """Create/edit a QR code. Field names mirror the model columns directly, so
    no Keys class is needed. Exactly-one-target is re-implemented here on the
    cleaned data (the model's clean() never runs via the UI save path)."""

    code = forms.SlugField(
        label="Code",
        help_text="Short token in the QR URL, e.g. 'spring-tabling' -> /qr/spring-tabling/.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    label = forms.CharField(
        label="Label",
        help_text="Human label, e.g. 'Spring 2026 tabling flyer'.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    campaign = forms.CharField(
        label="Campaign",
        required=False,
        help_text="Optional medium/source tag to break down scans, e.g. 'flyer' or 'table-tent'.",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    tree = forms.ModelChoiceField(
        label="Target: link tree",
        required=False,
        queryset=LinkTree.objects.order_by("title"),
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    item = forms.ModelChoiceField(
        label="Target: link tree item",
        required=False,
        queryset=LinkTreeItem.objects.select_related("tree").order_by("tree__title", "order"),
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    rawUrl = forms.URLField(
        label="Target: raw URL",
        required=False,
        help_text="Target an arbitrary URL instead of a tree/item.",
        widget=forms.URLInput(attrs={"class": "form-field w-full"}),
    )
    isActive = forms.BooleanField(
        label="Active",
        required=False,
        widget=forms.CheckboxInput(),
    )

    def __init__(self, *args, qr=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._qr = qr

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        existing = QRCode.objects.filter(code=code)
        if self._qr is not None:
            existing = existing.exclude(pk=self._qr.pk)
        if existing.exists():
            raise ValidationError("A QR code with that code already exists.")
        return code

    def clean(self):
        cleaned = super().clean()
        targets = [
            bool(cleaned.get("tree")),
            bool(cleaned.get("item")),
            bool(cleaned.get("rawUrl")),
        ]
        chosen = sum(1 for target in targets if target)
        if chosen != 1:
            raise ValidationError(
                "A QR code must point at exactly one target: a link tree, a link tree item, or a raw URL."
            )
        return cleaned


class ResolutionForm(forms.Form):
    """Submit a new resolution. Hand-rolled forms.Form (house style); the view
    calls Resolution.objects.create() directly. The ``kind`` radios are
    hand-rendered as type cards in the template, but the field validates the
    posted value against Kind.CHOICES."""

    title = forms.CharField(
        label="Title", max_length=200,
        widget=forms.TextInput(attrs={
            "class": "form-field w-full",
            "placeholder": "e.g. Endorse the Eastside BRT Plan",
        }),
    )
    kind = forms.ChoiceField(
        label="Type", choices=Resolution.Kind.CHOICES, widget=forms.RadioSelect,
    )
    # Restricted to upcoming meetings in __init__ (runtime ``now`` comparison
    # must not be frozen at import). Optional so a draft can exist before a
    # meeting is chosen.
    targetMeeting = forms.ModelChoiceField(
        label="Target meeting", queryset=PostedEvents.objects.none(), required=False,
        empty_label="Select a meeting (you can add one later)",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    text = forms.CharField(
        label="Resolution text",
        widget=forms.Textarea(attrs={
            "class": "form-field w-full", "rows": "12", "data-markdown-editor": "1",
            "placeholder": "Whereas...\n\nTherefore, be it resolved...",
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        now = datetime.datetime.now(datetime.UTC)
        self.fields["targetMeeting"].queryset = (
            PostedEvents.objects.filter(start__gt=now).order_by("start")
        )


class ResolutionEditForm(forms.Form):
    """Edit a resolution's text. A change to a locked resolution resets its
    sign-ons (Resolution.replaceText); the view requires confirmReset before
    applying such a change."""

    text = forms.CharField(
        label="Resolution text",
        widget=forms.Textarea(attrs={
            "class": "form-field w-full", "rows": "12", "data-markdown-editor": "1",
        }),
    )
    confirmReset = forms.BooleanField(required=False)


class ScheduleForm(forms.Form):
    """Place a resolution on an upcoming meeting agenda (GATHERING -> SCHEDULED)."""

    targetMeeting = forms.ModelChoiceField(
        label="Meeting", queryset=PostedEvents.objects.none(),
        empty_label="Select a meeting",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        now = datetime.datetime.now(datetime.UTC)
        self.fields["targetMeeting"].queryset = (
            PostedEvents.objects.filter(start__gt=now).order_by("start")
        )


class RecordVoteForm(forms.Form):
    """Record the membership's Yes / No / Abstain tally. The view applies
    Resolution.recordVote, which decides adopted vs rejected by the kind's vote
    threshold (two-thirds for amendments and endorsements, otherwise majority)."""

    votesYes = forms.IntegerField(
        label="Yes", min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-field", "min": "0"}),
    )
    votesNo = forms.IntegerField(
        label="No", min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-field", "min": "0"}),
    )
    votesAbstain = forms.IntegerField(
        label="Abstain", min_value=0, initial=0,
        widget=forms.NumberInput(attrs={"class": "form-field", "min": "0"}),
    )

    def clean(self):
        cleaned = super().clean()
        yes = cleaned.get("votesYes") or 0
        no = cleaned.get("votesNo") or 0
        if yes + no <= 0:
            raise ValidationError("Record at least one Yes or No vote.")
        return cleaned


class WithdrawForm(forms.Form):
    """Pull an in-flight resolution before it is voted on."""

    note = forms.CharField(
        label="Reason (optional)", required=False,
        widget=forms.TextInput(attrs={
            "class": "form-field w-full", "placeholder": "e.g. duplicate of an earlier resolution",
        }),
    )


class SupersedeForm(forms.Form):
    """Mark an adopted resolution as repealed, optionally pointing at the later
    resolution that replaced it."""

    replacement = forms.ModelChoiceField(
        label="Replaced by (optional)", queryset=Resolution.objects.none(), required=False,
        empty_label="Repealed (no replacement)",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
    )
    note = forms.CharField(
        label="Note (optional)", required=False,
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )

    def __init__(self, *args, excludePk=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Resolution.objects.filter(status=Resolution.Status.ADOPTED)
        if excludePk is not None:
            qs = qs.exclude(pk=excludePk)
        self.fields["replacement"].queryset = qs.order_by("-decidedAt")