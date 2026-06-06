from django.urls import path, include
from django.contrib import admin
from . import views
from . import accessViews
from . import eventViews
from . import linkTreeViews
from . import ownerViews

urlpatterns = [
    path("", views.index, name="index"),

    # --- Access requests (self-service group/permission grants) ---
    path("request-access", accessViews.request_access, name="request-access"),
    path("access-requests", accessViews.access_request_list, name="access-request-list"),
    path("access-requests/<int:id>/review", accessViews.review_access_request, name="review-access-request"),
    path("my-access", accessViews.my_access, name="my-access"),
    path("manage-access", accessViews.manage_access, name="manage-access"),
    path("manage-access/<int:userId>", accessViews.manage_access_user, name="manage-access-user"),
    path("manage-groups", accessViews.manage_groups, name="manage-groups"),
    path("manage-groups/<int:groupId>", accessViews.manage_group, name="manage-group"),
    path("manage-groups/<int:groupId>/member-search", accessViews.manage_group_member_search, name="manage-group-member-search"),
    path("manage-groups/<int:groupId>/delete", accessViews.manage_group_delete, name="manage-group-delete"),

    path("new-event", eventViews.new_event, name="new-event"),
    path("new-delegated-event", eventViews.new_delegated_event, name="new-delegated-event"),
    path("approve-delegated-event/<int:id>", eventViews.approve_delegated_event, name="approve-delegated-event"),
    path("delegated-event/<pk>/", eventViews.DelegatedEventDetailView.as_view(), name="delegated-event-detail"),
    path("delegated-events", eventViews.DelegatedEventListView.as_view(), name="delegated-event-list"),
    path("event/<pk>/", eventViews.PostedEventDetailView.as_view(), name="event-detail"),
    path("published-events", eventViews.PostedEventListView.as_view(), name="event-list"),

    # --- Event owners (the entities delegated events hang off) ---
    path("manage-event-owners", ownerViews.manage_event_owners, name="manage-event-owners"),
    path("manage-event-owners/new", ownerViews.create_event_owner, name="create-event-owner"),
    path("manage-event-owners/<int:ownerId>", ownerViews.manage_event_owner, name="manage-event-owner"),
    path("manage-event-owners/<int:ownerId>/authorizer-search", ownerViews.manage_event_owner_authorizer_search, name="manage-event-owner-authorizer-search"),
    path("manage-event-owners/<int:ownerId>/cancel-stuck-event/<int:eventId>", ownerViews.cancel_stuck_delegated_event, name="cancel-stuck-delegated-event"),

    # --- Link Tree (public: tree page + tracked click/scan redirects) ---
    path("t/<slug:slug>/", linkTreeViews.public_tree, name="link-tree"),
    path("go/<int:item_id>/", linkTreeViews.go, name="link-go"),
    path("qr/<slug:code>/", linkTreeViews.qr_redirect, name="qr-redirect"),
    # --- Link Tree (gated: QR image generation + metrics) ---
    path("qr/<slug:code>/image", linkTreeViews.qr_image, name="qr-image"),
    path("link-metrics", linkTreeViews.link_metrics, name="link-metrics"),
    path("link-metrics/<slug:slug>", linkTreeViews.link_metrics, name="link-metrics-tree"),
    path("link-metrics/<slug:slug>.csv", linkTreeViews.link_metrics_csv, name="link-metrics-csv"),
    # --- Link Tree (gated: in-app management UI) ---
    path("manage-link-trees", linkTreeViews.manage_link_tree_list, name="manage-link-tree-list"),
    path("manage-link-trees/<int:treeId>", linkTreeViews.manage_link_tree_edit, name="manage-link-tree-edit"),
    path("manage-link-trees/<int:treeId>/reorder", linkTreeViews.manage_link_tree_item_reorder, name="manage-link-tree-item-reorder"),
    path("manage-link-trees/<int:treeId>/items/new", linkTreeViews.manage_link_tree_item_edit, name="manage-link-tree-item-new"),
    path("manage-link-trees/<int:treeId>/items/<int:itemId>", linkTreeViews.manage_link_tree_item_edit, name="manage-link-tree-item-edit"),
    path("manage-qr-codes", linkTreeViews.manage_qr_code_list, name="manage-qr-code-list"),
    path("manage-qr-codes/new", linkTreeViews.manage_qr_code_edit, name="manage-qr-code-new"),
    path("manage-qr-codes/<slug:code>", linkTreeViews.manage_qr_code_edit, name="manage-qr-code-edit"),

    # --- Domain landing pages (/events, /link-trees, /access) ---
    # Deliberately last: any single-segment path not claimed above is tried as
    # a domain slug; views.domain 404s the unknown ones. Adding a Domain to
    # views.DOMAINS is all it takes to route a new landing page.
    path("<slug:domainSlug>", views.domain, name="domain"),
]
