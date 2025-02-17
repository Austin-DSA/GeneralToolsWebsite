from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("new-event", views.new_event, name="new-event"),
]
