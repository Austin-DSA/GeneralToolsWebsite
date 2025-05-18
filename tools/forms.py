import typing
import pytz
import logging
import datetime
import copy

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .EventAutomation import EventAutomationDriver

from .models import User, EventOwners

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


class NewEventForm(forms.Form):
    class Keys:
        TITLE = "title"
        DESCRIPTION = "description"
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

    def __init__(self, currentUser: User, *args, **kwargs):
        super().__init__(*args, **kwargs)
        owners = self.getOwners(currentUser=currentUser)
        self.fields["owner"] = forms.ChoiceField(choices=owners, label="Event Owner", widget=forms.Select(attrs={"class": "form-field w-full"}))

    def getOwners(self, currentUser: User):
        owners = {}
        for eventOwner in currentUser.eventAuthorizations:
            if eventOwner.isActive:
                owners[eventOwner.name] = eventOwner.name
        return owners

    owner = forms.ChoiceField(
        label="Event Owner",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={}
    )
    title = forms.CharField(
        label="Event title",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    description = forms.CharField(
        label="Description",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    timezone = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={timezone: timezone for timezone in pytz.all_timezones},
        initial="America/Chicago",
    )
    startTime = forms.DateTimeField(
        label="Start time",
        widget=forms.DateTimeInput(attrs={"class": "form-field w-full"}),
    )
    endTime = forms.DateTimeField(
        label="End time",
        widget=forms.DateTimeInput(attrs={"class": "form-field w-full"}),
    )
    instructions = forms.CharField(
        label="Instructions",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    locationName = forms.CharField(
        label="Location name",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    address = forms.CharField(
        label="Address", widget=forms.TextInput(attrs={"class": "form-field w-full"})
    )
    city = forms.CharField(
        label="City",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        initial="Austin",
    )
    choices = {state: state for state in STATES}
    state = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices=choices,
        initial="TX",
    )
    country = forms.CharField(
        label="Country",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        initial="US",
    )
    zipcode = forms.IntegerField(
        label="Zip code", widget=forms.NumberInput(attrs={"class": "form-field w-full"})
    )
    ignoreResolveableConflics = forms.BooleanField(
        label="Ignore Resolveable Conflicts",
        widget=forms.CheckboxInput(),
        required=False,
    )

    # TODO: Figure out how to get these validators to show up on screen when filling out the form
    # def clean_country(self):
    #     data = self.cleaned_data[NewEventForm.Keys.COUNTRY]
    #     if data != "US":
    #         raise ValidationError(_("Only support US for country field"))
    #     return data

    # TODO: Fix
    # This kept forgetting the timezone info and replacing it with UTC, it was probably fine but until we can figure it out leaving out
    # The automator will also check for this and print an error, its just uglier
    # def clean_startTime(self):
    #     start = self.cleaned_data[NewEventForm.Keys.START_TIME]
    #     # Copy so we don't lose timezone info
    #     startCopy = copy.deepcopy(start)
    #     if startCopy.astimezone(pytz.utc) < datetime.datetime.now(tz=pytz.utc):
    #         raise ValidationError(_("Start Time must be in the future"))
    #     return start

    # def clean_endTime(self):
    #     end = self.cleaned_data[NewEventForm.Keys.END_TIME]
    #     # Copy so we don't lose timezone info
    #     endCopy = copy.deepcopy(end)
    #     if endCopy.astimezone(pytz.utc) < datetime.datetime.now(tz=pytz.utc):
    #         raise ValidationError(_("End Time must be in the future"))
    #     return end

    def clean_zipcode(self):
        data = self.cleaned_data[NewEventForm.Keys.ZIP_CODE]
        zip_str = str(data)

        if len(zip_str) == 5:
            return data

        else:
            raise ValidationError(_("Zip code must be five digits long"))

    def convertToEventInfo(self) -> typing.Optional[EventAutomationDriver.EventInfo]:
        if not self.is_valid():
            return None
        formData = self.cleaned_data
        timezoneStr = formData[NewEventForm.Keys.TIMEZONE]
        timezone = pytz.timezone(timezoneStr)
        start: datetime.datetime = formData[NewEventForm.Keys.START_TIME]
        end: datetime.datetime = formData[NewEventForm.Keys.END_TIME]
        # Django apparently does so auto-magic and will make the date times timezone aware based on the existence of the timezone field in the form
        # Defensively we will set the timezone only if the dates are naive
        # Update - Its no longer doing it, no idea what I changed but giving up for now, just going to localize if its naive and convert otherwise
        # BTW I hate timezones
        if start.tzinfo is None or start.tzinfo.utcoffset(start) is None:
            start = timezone.localize(start)
        else:
            start = start.replace(tzinfo=timezone)
        if end.tzinfo is None or end.tzinfo.utcoffset(end) is None:
            end = timezone.localize(end)
        else:
            end = end.replace(tzinfo=timezone)

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
        )
        return eventInfo


class NewDelegatedEventForm(forms.Form):
    class Keys:
        TITLE = "title"
        DESCRIPTION = "description"
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


    owner = forms.ChoiceField(
        label="Event Owner",
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={ x.name : x.name for x in EventOwners.objects.all()}
    )
    title = forms.CharField(
        label="Event title",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    description = forms.CharField(
        label="Description",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    timezone = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={timezone: timezone for timezone in pytz.all_timezones},
        initial="America/Chicago",
    )
    startTime = forms.DateTimeField(
        label="Start time",
        widget=forms.DateTimeInput(attrs={"class": "form-field w-full"}),
    )
    endTime = forms.DateTimeField(
        label="End time",
        widget=forms.DateTimeInput(attrs={"class": "form-field w-full"}),
    )
    instructions = forms.CharField(
        label="Instructions",
        widget=forms.Textarea(attrs={"rows": "5", "class": "form-field w-full"}),
    )
    locationName = forms.CharField(
        label="Location name",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    address = forms.CharField(
        label="Address", widget=forms.TextInput(attrs={"class": "form-field w-full"})
    )
    city = forms.CharField(
        label="City",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        initial="Austin",
    )
    choices = {state: state for state in STATES}
    state = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices=choices,
        initial="TX",
    )
    country = forms.CharField(
        label="Country",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
        initial="US",
    )
    zipcode = forms.IntegerField(
        label="Zip code", widget=forms.NumberInput(attrs={"class": "form-field w-full"})
    )
    ignoreResolveableConflics = forms.BooleanField(
        label="Ignore Resolveable Conflicts",
        widget=forms.CheckboxInput(),
        required=False,
    )

    # TODO: Figure out how to get these validators to show up on screen when filling out the form
    # def clean_country(self):
    #     data = self.cleaned_data[NewEventForm.Keys.COUNTRY]
    #     if data != "US":
    #         raise ValidationError(_("Only support US for country field"))
    #     return data

    # TODO: Fix
    # This kept forgetting the timezone info and replacing it with UTC, it was probably fine but until we can figure it out leaving out
    # The automator will also check for this and print an error, its just uglier
    # def clean_startTime(self):
    #     start = self.cleaned_data[NewEventForm.Keys.START_TIME]
    #     # Copy so we don't lose timezone info
    #     startCopy = copy.deepcopy(start)
    #     if startCopy.astimezone(pytz.utc) < datetime.datetime.now(tz=pytz.utc):
    #         raise ValidationError(_("Start Time must be in the future"))
    #     return start

    # def clean_endTime(self):
    #     end = self.cleaned_data[NewEventForm.Keys.END_TIME]
    #     # Copy so we don't lose timezone info
    #     endCopy = copy.deepcopy(end)
    #     if endCopy.astimezone(pytz.utc) < datetime.datetime.now(tz=pytz.utc):
    #         raise ValidationError(_("End Time must be in the future"))
    #     return end

    def clean_zipcode(self):
        data = self.cleaned_data[NewEventForm.Keys.ZIP_CODE]
        zip_str = str(data)

        if len(zip_str) == 5:
            return data

        else:
            raise ValidationError(_("Zip code must be five digits long"))

    def convertToEventInfo(self) -> typing.Optional[EventAutomationDriver.EventInfo]:
        if not self.is_valid():
            return None
        formData = self.cleaned_data
        timezoneStr = formData[NewEventForm.Keys.TIMEZONE]
        timezone = pytz.timezone(timezoneStr)
        start: datetime.datetime = formData[NewEventForm.Keys.START_TIME]
        end: datetime.datetime = formData[NewEventForm.Keys.END_TIME]
        # Django apparently does so auto-magic and will make the date times timezone aware based on the existence of the timezone field in the form
        # Defensively we will set the timezone only if the dates are naive
        # Update - Its no longer doing it, no idea what I changed but giving up for now, just going to localize if its naive and convert otherwise
        # BTW I hate timezones
        if start.tzinfo is None or start.tzinfo.utcoffset(start) is None:
            start = timezone.localize(start)
        else:
            start = start.replace(tzinfo=timezone)
        if end.tzinfo is None or end.tzinfo.utcoffset(end) is None:
            end = timezone.localize(end)
        else:
            end = end.replace(tzinfo=timezone)

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
        )
        return eventInfo