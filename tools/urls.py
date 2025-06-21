from django.urls import path, include
from django.contrib import admin
from . import views
from . import eventViews

# TODO: Add in UI views for all the auth urls
urlpatterns = [
    path("", views.index, name="index"),
    path("new-event", eventViews.new_event, name="new-event"),
    path("new-delegated-event", eventViews.new_delegated_event, name="new-delegated-event"),
    path("approve-delegated-event/<int:id>", eventViews.approve_delegated_event, name="approve-delegated-event"),
    path("delegated-event/<pk>/", eventViews.DelegatedEventDetailView.as_view(), name="delegated-event-detail"),
    path("accounts/", include("django.contrib.auth.urls")),
]
