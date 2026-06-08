from django.contrib import admin
from django.urls import include, path

import events.eventViews as eventViews
import voting.views as votingViews
from . import  views

# TODO: Add in UI views for all the auth urls
urlpatterns = [
    path("", views.index, name="index"),
    path("new-event", eventViews.new_event, name="new-event"),
    path(
        "new-delegated-event",
        eventViews.new_delegated_event,
        name="new-delegated-event",
    ),
    path(
        "approve-delegated-event/<int:id>",
        eventViews.approve_delegated_event,
        name="approve-delegated-event",
    ),
    path(
        "delegated-event/<pk>/",
        eventViews.DelegatedEventDetailView.as_view(),
        name="delegated-event-detail",
    ),
    path(
        "delegated-events",
        eventViews.DelegatedEventListView.as_view(),
        name="delegated-event-list",
    ),
    path(
        "event/<pk>/", eventViews.PostedEventDetailView.as_view(), name="event-detail"
    ),
    path("events", eventViews.PostedEventListView.as_view(), name="event-list"),

    # Guest URLs
    path("guestLogin", views.guestLogin, name="guest-login"),
    path("guestDash", views.guestDashBoard, name="guest-dash"),

    # Resolution URLS
    path("resolutions", votingViews.ResolutionListView.as_view(), name="resolution-list"),
    path("resolution/<pk>/", votingViews.ResolutionDetailView.as_view(), name="resolution-detail"),
    path("resolution/<int:id>/validate", votingViews.VoteValidationEndpoint.as_view(), name="resolution-validate"),
    path("resolution/<int:id>/emailResults", votingViews.EmailResolutionResults.as_view(), name="resolution-email-results"),
    path("resolution/<int:id>/emailFailedValidations", votingViews.EmailInvalidVotes.as_view(), name="resolution-email-invalid"),
    path("resolution/<int:id>/results", votingViews.ResolutionResults.as_view(), name="resolution-results"),
    path("resolution/new", votingViews.NewResolution.as_view(), name="resolution-new"),
    path("resolution/<int:id>/edit", votingViews.EditResolution.as_view(), name="resolution-edit"),

    # Voting URLS
    path("voting/guestBallot", votingViews.guestBallotView, name="guest-ballot"),
    path("voting/submit/<int:id>", votingViews.guestProcessVote, name="guest-submit-vote"),

]
