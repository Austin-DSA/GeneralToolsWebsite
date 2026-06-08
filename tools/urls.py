from django.contrib import admin
from django.urls import include, path

import events.eventViews as eventViews
import voting.views as votingViews
from . import views
from . import eventViews
from . import linkTreeViews

# TODO: Add in UI views for all the auth urls
urlpatterns = [
    path("", views.index, name="index"),
    
    # Guest URLs
    path("guestLogin", views.guestLogin, name="guest-login"),
    path("guestDash", views.guestDashBoard, name="guest-dash"),

    # Resolution URLs
    path("resolutions", votingViews.ResolutionListView.as_view(), name="resolution-list"),
    path("resolution/<pk>/", votingViews.ResolutionDetailView.as_view(), name="resolution-detail"),
    path("resolution/<int:id>/validate", votingViews.VoteValidationEndpoint.as_view(), name="resolution-validate"),
    path("resolution/<int:id>/emailResults", votingViews.EmailResolutionResults.as_view(), name="resolution-email-results"),
    path("resolution/<int:id>/emailFailedValidations", votingViews.EmailInvalidVotes.as_view(), name="resolution-email-invalid"),
    path("resolution/<int:id>/results", votingViews.ResolutionResults.as_view(), name="resolution-results"),
    path("resolution/new", votingViews.NewResolution.as_view(), name="resolution-new"),
    path("resolution/<int:id>/edit", votingViews.EditResolution.as_view(), name="resolution-edit"),

    # Voting URLs
    path("voting/guestBallot", votingViews.guestBallotView, name="guest-ballot"),
    path("voting/submit/<int:id>", votingViews.guestProcessVote, name="guest-submit-vote"),

    # Event URLs
    path("new-event", eventViews.new_event, name="new-event"),
    path("new-delegated-event", eventViews.new_delegated_event, name="new-delegated-event"),
    path("approve-delegated-event/<int:id>", eventViews.approve_delegated_event, name="approve-delegated-event"),
    path("delegated-event/<pk>/", eventViews.DelegatedEventDetailView.as_view(), name="delegated-event-detail"),
    path("delegated-events", eventViews.DelegatedEventListView.as_view(), name="delegated-event-list"),
    path("event/<pk>/", eventViews.PostedEventDetailView.as_view(), name="event-detail"),
    path("events", eventViews.PostedEventListView.as_view(), name="event-list"),

    # --- Link Tree (public: tree page + tracked click/scan redirects) ---
    path("t/<slug:slug>/", linkTreeViews.public_tree, name="link-tree"),
    path("go/<int:item_id>/", linkTreeViews.go, name="link-go"),
    path("qr/<slug:code>/", linkTreeViews.qr_redirect, name="qr-redirect"),
    # --- Link Tree (gated: QR image generation + metrics) ---
    path("qr/<slug:code>/image", linkTreeViews.qr_image, name="qr-image"),
    path("link-metrics", linkTreeViews.link_metrics, name="link-metrics"),
    path("link-metrics/<slug:slug>", linkTreeViews.link_metrics, name="link-metrics-tree"),
    path("link-metrics/<slug:slug>.csv", linkTreeViews.link_metrics_csv, name="link-metrics-csv"),
]
