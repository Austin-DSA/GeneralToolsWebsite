from django import forms
from enum import StrEnum
from .models import Resolution

import dataclasses
import pytz
from datetime import datetime
from typing import override

import django.utils.timezone

@dataclasses.dataclass
class ResolutionFormData:
    name : str
    author: str
    textUrl: str
    timezone: str
    voteOpenUTC: datetime
    voteCloseUTC: datetime
    id: int | None

class NewResolutionForm(forms.Form):
    class Keys:
        NAME = "name"
        AUTHOR = "author"
        TEXT_URL = "textUrl"
        TIMEZONE = "timezone"
        VOTE_OPEN = "voteOpen"
        VOTE_CLOSE = "voteClose"

    name = forms.CharField(
        max_length=500,
        label="Resolution Name",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )

    author = forms.CharField(
        max_length=500,
        label="Resolution Author",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )
    
    textUrl = forms.URLField(
        label="Resolution Text Link",
        widget=forms.TextInput(attrs={"class": "form-field w-full"}),
    )

    timezone = forms.ChoiceField(
        widget=forms.Select(attrs={"class": "form-field w-full"}),
        choices={timezone: timezone for timezone in pytz.all_timezones},
        initial="America/Chicago",
    )
    voteOpen = forms.DateTimeField(
        label="Voting Open",
        widget=forms.DateTimeInput(attrs={"class": "form-field w-full"}),
    )
    voteClose = forms.DateTimeField(
        label="Voting Close",
        widget=forms.DateTimeInput(attrs={"class": "form-field w-full"}),
    )

    def getData(self) -> ResolutionFormData | None:
        if not self.is_valid():
            return None
        formData = self.cleaned_data
        timezoneStr = formData[NewResolutionForm.Keys.TIMEZONE]
        timezone = pytz.timezone(timezoneStr)

        naiiveVoteOpen = formData[NewResolutionForm.Keys.VOTE_OPEN]
        localizedVoteOpen = django.utils.timezone.make_aware(naiiveVoteOpen, timezone=timezone)
        utcVoteOpen = django.utils.timezone.localtime(localizedVoteOpen, timezone=django.utils.timezone.utc)

        naiiveVoteClose = formData[NewResolutionForm.Keys.VOTE_CLOSE]
        localizedVoteClose = django.utils.timezone.make_aware(naiiveVoteClose, timezone=timezone)
        utcVoteClose = django.utils.timezone.localtime(localizedVoteClose, timezone=django.utils.timezone.utc)

        return ResolutionFormData(
            name=formData[NewResolutionForm.Keys.NAME],
            textUrl=formData[NewResolutionForm.Keys.TEXT_URL],
            timezone=timezoneStr,
            voteOpenUTC=utcVoteOpen,
            voteCloseUTC=utcVoteClose,
            author=formData[NewResolutionForm.Keys.AUTHOR]
        )
        

class EditResolutionForm(NewResolutionForm):
    class Keys:
        RESOLUTION_ID = "resolutionId"

    resolutionId = forms.IntegerField(
        widget=forms.HiddenInput()
    )

    @override
    def getData(self) -> ResolutionFormData | None:
        data = super().getData()
        if data is None:
            return None
        data.id = self.cleaned_data[EditResolutionForm.Keys.RESOLUTION_ID]
        return data

    @staticmethod
    def getFormFromResolution(resolution: Resolution) -> EditResolutionForm:
        return EditResolutionForm(
            initial={
                EditResolutionForm.Keys.RESOLUTION_ID : resolution.id,
                NewResolutionForm.Keys.NAME : resolution.name,
                NewResolutionForm.Keys.TEXT_URL : resolution.textUrl,
                NewResolutionForm.Keys.TIMEZONE : resolution.timezone,
                NewResolutionForm.Keys.VOTE_OPEN : resolution.getVoteOpenLocalized(),
                NewResolutionForm.Keys.VOTE_CLOSE : resolution.getVoteCloseLocalized(),
                }
            )

class VoteResolutionForm(forms.Form):
    class Keys:
        VOTE = "vote"

    class VoteChoices(StrEnum):
        YES = "YES"
        NO = "NO"
        ABSTAIN = "ABSTAIN"

    vote = forms.ChoiceField(
        choices=[c.value for c in VoteChoices], label="Should Austin DSA Adopt this Resolution?"
    )

    def getVote(self) -> VoteChoices | None:
        if not self.is_valid():
            return None
        return self.cleaned_data[VoteResolutionForm.Keys.VOTE]
