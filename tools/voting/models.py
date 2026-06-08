import datetime

import pytz
from django.core.validators import URLValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy
import utils

class Resolution(models.Model):
    name = models.CharField(max_length=500)
    # Using a text field with a url validator becuase don't want a max length requirement like URLField has
    textUrl = models.TextField(validators=[URLValidator()])
    author = models.CharField(max_length=100)
    votingOpen = models.DateTimeField()
    votingClose = models.DateTimeField()
    timezone = models.CharField(max_length=50)

    whenLastValidated = models.DateTimeField(blank=True)
    lastValidatedCountYes = models.IntegerField(default=0)
    lastValidatedCountAbstain = models.IntegerField(default=0)
    lastValidatedCountNo = models.IntegerField(default=0)

    def isEditable(self) -> bool:
        now = datetime.datetime.now(datetime.UTC)
        return now < self.votingOpen

    def canValidate(self) -> bool:
        now = datetime.datetime.now(datetime.UTC)
        return now > self.votingClose

    def isOpen(self) -> bool:
        now = datetime.datetime.now(datetime.UTC)
        return now > self.votingOpen and now < self.votingClose

    def isOpenStr(self) -> str:
        return "YES" if self.isOpen() else "NO"

    def getVoteOpenLocalized(self) -> datetime.datetime:
        return utils.localizeDate(self.votingOpen,self.timezone)
    
    def getVoteCloseLocalized(self) -> datetime.datetime:
        return utils.localizeDate(self.votingClose,self.timezone)

    def votingOpenLocalizedStr(self) -> str:
        return self.getVoteOpenLocalized().strftime(utils.DATE_TIME_FORMAT)

    def votingCloseLocalizedStr(self) -> str:
        return self.getVoteCloseLocalized().strftime(utils.DATE_TIME_FORMAT)

    def whenLastValidatedStr(self) -> str:
        if self.whenLastValidated is None:
            return "NEVER"
        return self.whenLastValidated.strftime(utils.DATE_TIME_FORMAT)

    def passedLastValidated(self):
        return self.reachedQourumLastValidated() and self.lastValidatedCountYes > self.lastValidatedCountNo

    def reachedQourumLastValidated(self):
        #TODO: Don't hard code qorum
        return self.lastValidatedCountAbstain+self.lastValidatedCountNo+self.lastValidatedCountYes >= 60

    def status(self) -> str:
        if self.whenLastValidated is None:
            return "Not Validated"
        if not self.reachedQourumLastValidated():
            return "No Qourum"
        if self.passedLastValidated():
            return "Passed"
        return "Failed"


class ResolutionVote(models.Model):

    class VerificationError(models.IntegerChoices):
        NO_ERROR = 0, gettext_lazy("None")
        NOT_FOUND = 1, gettext_lazy("Not Found in List")
        INVALID_API_RESPONSE = 2, gettext_lazy("Invalid Response from Action Network")
        MISSING_REQUIRED_CUSTOM_FIELDS = 3, gettext_lazy("Missing required custom fields in list")
        MULTIPLE_RECORDS_RETURNED = 4, gettext_lazy("Multiple conflicting records were found")
        EXPIRED = 5, gettext_lazy("Membership has lapsed")
        INCORRECT_CHAPTER = 6, gettext_lazy("Incorrect Chapter")
        UNKOWN = 7, gettext_lazy("Unexepected Error Occured")

    class VoteChoices(models.IntegerChoices):
        ABSTAIN = 0, gettext_lazy("Abstain")
        NO = 1, gettext_lazy("No")
        YES = 2, gettext_lazy("Yes")

    @staticmethod
    def getChoiceForString(voteStr: str) -> VoteChoices | None:
        cleanVote = voteStr.strip().lower()
        if cleanVote == "abstain":
            return ResolutionVote.VoteChoices.ABSTAIN
        if cleanVote == "no":
            return ResolutionVote.VoteChoices.NO
        if cleanVote == "yes":
            return ResolutionVote.VoteChoices.YES
        return None

    vote = models.IntegerField(choices=VoteChoices, default=0)
    resolution = models.ForeignKey(Resolution, on_delete=models.SET_NULL)
    # Don't need a reference to the user since we will need to store email/name for guest votes anyway
    # If in the future we want to collect per user votes could use email, that way it also captures their guest votes
    # Far future we could remove guest voting if we have an actual usable auth system, then we can migrate this to a user reference
    email = models.EmailField()
    name = models.CharField(max_length=500)

    casted = models.DateTimeField(auto_now_add=True)
    checkedForVerification = models.BooleanField(default=False)
    whenVerified = models.DateTimeField(blank=True)
    verificationError = models.IntegerField(choices=VerificationError, default=0)