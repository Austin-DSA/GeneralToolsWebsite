from django.urls import path, include
from django.contrib import admin
from . import views
from . import eventViews
from . import linkTreeViews

# TODO: Add in UI views for all the auth urls
urlpatterns = [
    path("", views.index, name="index"),
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
