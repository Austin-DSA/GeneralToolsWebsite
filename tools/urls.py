from django.urls import path, include
from django.contrib import admin
from . import views

#TODO: Add in UI views for all the auth urls
urlpatterns = [
    path("", views.index, name="index"),
    path("new-event", views.new_event, name="new-event"),
    path("accounts/", include("django.contrib.auth.urls")),
]
