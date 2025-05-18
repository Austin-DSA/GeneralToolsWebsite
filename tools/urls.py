from django.urls import path, include
from django.contrib import admin
from . import views

# TODO: Add in UI views for all the auth urls
urlpatterns = [
    path("", views.index, name="index"),
    path("new-event", views.new_event, name="new-event"),
    path("new-delegated-event", views.new_delegated_event, name="new-delegated-event"),
    path("approve-delegated-event/<int:id>", views.approve_delegated_event, name="approve-delegated-event"),
    # path("delegated-event/<int:id>")
    path("accounts/", include("django.contrib.auth.urls")),
]
