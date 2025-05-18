from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    pass

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

    creator = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True)
    authorizer = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True)
    reasonApproved = models.TextField()

    owner = models.ForeignKey(EventOwners, on_delete=models.SET_NULL, blank=True, null=True)

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

    locationName = models.CharField(max_length=500)
    streetAddress = models.CharField(max_length=500)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    zip = models.CharField(max_length=10)
    country = models.CharField(max_length=100)

    description = models.TextField()
    instructions = models.TextField()

    dateCreated = models.DateTimeField()

    creator = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True)
    owner = models.ForeignKey(EventOwners, on_delete=models.SET_NULL, blank=True, null=True)

    state = models.IntegerField()
    reason = models.TextField()