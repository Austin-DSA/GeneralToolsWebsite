import datetime

import pytz
from django.contrib.auth.models import AbstractUser
from django.core.validators import URLValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy

from . import utils
from .EventAutomation.EventAutomationDriver import EventInfo

from .voting.models import Resolution, ResolutionVote

class User(AbstractUser):
    def getUserNameString(self) -> str:
        return f"{self.first_name} {self.last_name} - {self.email}"


class EventOwners(models.Model):
    name = models.CharField(max_length=100, unique=True)
    authorizers = models.ManyToManyField(User, related_name="eventAuthorizations")
    expiration = models.DateTimeField()

    def isActive(self):
        if datetime.datetime.now(datetime.UTC) < self.expiration:
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
    anManageLink = models.TextField(validators=[URLValidator()])
    anShareLink = models.TextField(validators=[URLValidator()])
    gCalLink = models.TextField(validators=[URLValidator()])
    zoomLink = models.TextField(validators=[URLValidator()])
    zoomAccount = models.CharField(max_length=100)
    zoomRequired = models.BooleanField(default=True)

    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="postedEventCreator",
    )
    authorizer = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="postedEventAuthorizer",
    )
    reason = models.TextField()

    owner = models.ForeignKey(
        EventOwners, on_delete=models.SET_NULL, blank=True, null=True
    )

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
        return reverse("event-detail", kwargs={"pk": self.id})

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
        return EventInfo(
            title=self.title,
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
            zoomRequired=self.zoomRequired,
        )


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

    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="delegatedEventCreator",
    )
    owner = models.ForeignKey(
        EventOwners, on_delete=models.SET_NULL, blank=True, null=True
    )
    approver = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="delegatedEventApprover",
    )

    status = models.IntegerField()
    reason = models.TextField(blank=True)

    zoomRequired = models.BooleanField(default=True)

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

    def getUrl(self) -> str:
        if self.status == DelegatedEvents.Status.REQUESTED:
            return reverse("approve-delegated-event", kwargs={"id": self.id})
        return reverse("delegated-event-detail", kwargs={"pk": self.id})

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
        return EventInfo(
            title=self.title,
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
            zoomRequired=self.zoomRequired,
        )
