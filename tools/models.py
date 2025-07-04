from django.db import models
from django.contrib.auth.models import AbstractUser
from .EventAutomation.EventAutomationDriver import EventInfo
import datetime
import pytz
from django.urls import reverse

class User(AbstractUser):
    def getUserNameString(self) -> str:
        return f"{self.first_name} {self.last_name} - {self.email}"

class EventOwners(models.Model):
    name = models.CharField(max_length=100, unique=True)
    authorizers = models.ManyToManyField(User, related_name="eventAuthorizations")
    isActive = models.BooleanField()
    # TODO: Add in automatic expiration for things like campaign

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

    creator = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="postedEventCreator")
    authorizer = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True, related_name="postedEventAuthorizer")
    reasonApproved = models.TextField()

    owner = models.ForeignKey(EventOwners, on_delete=models.SET_NULL, blank=True, null=True)

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
        return reverse("event-detail", kwargs={"id" : self.id})
    
    def getStartLocalized(self) -> datetime.datetime:
        utcTime = self.start
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.replace(tzinfo=timezone)
        return localTime
    
    def getEndLocalized(self) -> datetime.datetime:
        utcTime = self.end
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.replace(tzinfo=timezone)
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
                         country=self.country)

# List of events that were denied to be created
# Originally thought about moving events from delegated events from tables based on when they are approved/denied
# class DeniedDelegatedEvents(models.Model):
#     title = models.CharField(max_length=500)
#     start = models.DateTimeField()
#     end = models.DateTimeField()

#     locationName = models.CharField(max_length=500)
#     streetAddress = models.CharField(max_length=500)
#     city = models.CharField(max_length=100)
#     state = models.CharField(max_length=100)
#     zip = models.CharField(max_length=10)
#     country = models.CharField(max_length=100)

#     description = models.TextField()
#     instructions = models.TextField()

#     dateCreated = models.DateTimeField()

#     creator = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True)
#     authorizer = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True)
#     owner = models.ForeignKey(EventOwners, on_delete=models.SET_NULL, blank=True, null=True)

#     reasonDenied = models.TextField()

 # List of events that have been created to be delegated to an authorizer
 # There will be duplication with approved events here and the events in PostedEvents, PostedEvents should effectively be the truth of all published events
class DelegatedEvents(models.Model):
    class State:
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

    def getStatusAsString(self) -> str:
        if self.status == DelegatedEvents.State.REQUESTED:
            return "Requested"
        elif self.status == DelegatedEvents.State.DENIED:
            return "Denied"
        elif self.status == DelegatedEvents.State.APPROVED:
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
        if self.state == DelegatedEvents.State.REQUESTED:
            return reverse("approve-delegated-event", kwargs={ "id" :self.id})
        return reverse("delegated-event-detail", kwargs={"id" : self.id})
    
    def getStartLocalized(self) -> datetime.datetime:
        utcTime = self.start
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.replace(tzinfo=timezone)
        return localTime
    
    def getEndLocalized(self) -> datetime.datetime:
        utcTime = self.end
        # If naiive add in the UTC info
        if utcTime.tzinfo is None or utcTime.tzinfo.utcoffset(utcTime) is None:
            utcTimezone = pytz.utc
            utcTime = utcTimezone.localize(utcTime)
        timezone = pytz.timezone(self.timezone)
        localTime = utcTime.replace(tzinfo=timezone)
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
                         country=self.country)