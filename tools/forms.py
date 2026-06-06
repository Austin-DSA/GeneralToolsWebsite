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
from .models import User, EventOwners, AccessRequests

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

    GROUP_PREFIX = "g"
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
        groupChoices = [
            (f"{self.GROUP_PREFIX}:{group.id}", group.name)
            for group in Group.objects.order_by("name")
        ]
        permissionChoices = [
            (f"{self.PERMISSION_PREFIX}:{permission.id}", permission.name)
            for permission in permissions.getRequestablePermissions()
        ]
        self.fields[self.Keys.TARGET].choices = [
            ("Groups", groupChoices),
            ("Permissions", permissionChoices),
        ]

    def clean(self):
        cleanedData = super().clean()
        targetValue = cleanedData.get(self.Keys.TARGET)
        if not targetValue:
            return cleanedData
        kind, _, targetId = targetValue.partition(":")
        group = None
        permission = None
        # The ChoiceField already validated the value against the rendered
        # choices, but the target may have been deleted since the form loaded
        if kind == self.GROUP_PREFIX:
            group = Group.objects.filter(id=targetId).first()
            if group is None:
                raise ValidationError("The selected option is no longer available.")
            if self.user.groups.filter(id=group.id).exists():
                raise ValidationError(f"You are already a member of {group.name}.")
            alreadyPending = AccessRequests.objects.filter(
                requester=self.user, group=group, status=AccessRequests.Status.REQUESTED
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
        cleanedData["group"] = group
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