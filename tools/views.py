import logging
import dataclasses

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from . import permissions
from .models import AccessRequests
import dataclasses

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class PageOption:
    href : str
    title : str
    permission : str | None  # None = visible to every logged-in user
    icon : str = ""
    description : str = ""
    category : str = "Tools"

    def getOptionDict(self):
        return {
            "href" : self.href,
            "title": self.title,
            "icon": self.icon,
            "description": self.description,
            "category": self.category,
        }

# Section order on the home page follows CATEGORIES; a section only renders
# if the user can see at least one of its tools.
CATEGORIES = ["Events", "Link Trees", "Access & Account"]

PAGES = [
    PageOption(href="new-event", title="Create an Event", permission=permissions.PUBLISH_EVENT,
               icon="📅", category="Events",
               description="Publish an event to Zoom, Action Network, and Google Calendar in one go."),
    PageOption(href="new-delegated-event", title="Create Delegated Event Request", permission=permissions.REQUEST_DELEGATED_EVENT,
               icon="📨", category="Events",
               description="Draft an event for an owner's authorizers to approve and publish."),
    PageOption(href="events", title="View Published Events", permission=permissions.VIEW_PUBLISHED_EVENTS,
               icon="🗂️", category="Events",
               description="Everything that has been published, with all the links."),
    PageOption(href="delegated-events", title="View Delegated Events", permission=permissions.VIEW_DELEGATED_EVENTS,
               icon="📥", category="Events",
               description="Track event requests and where they are in review."),
    PageOption(href="/admin/tools/linktree/", title="Manage Link Trees", permission=permissions.MANAGE_LINK_TREE,
               icon="🔗", category="Link Trees",
               description="Edit the chapter's link pages, items, and QR codes."),
    PageOption(href="link-metrics", title="Link Tree Metrics", permission=permissions.VIEW_LINK_METRICS,
               icon="📊", category="Link Trees",
               description="Click and scan analytics for every link tree."),
    PageOption(href="my-access", title="My Access", permission=None,
               icon="🪪", category="Access & Account",
               description="The groups you belong to and the permissions you currently have."),
    PageOption(href="request-access", title="Request Access", permission=None,
               icon="🔑", category="Access & Account",
               description="Ask to join a group or get a permission — approvers are emailed a review link."),
    PageOption(href="access-requests", title="View Access Requests", permission=None,
               icon="📬", category="Access & Account",
               description="Your requests and their status, plus any waiting on your review."),
    PageOption(href="manage-access", title="Manage Member Access", permission=permissions.APPROVE_ACCESS_REQUEST,
               icon="🛂", category="Access & Account",
               description="Grant or revoke groups and permissions directly, without a request."),
    PageOption(href="manage-groups", title="Manage Groups", permission=permissions.APPROVE_ACCESS_REQUEST,
               icon="👥", category="Access & Account",
               description="Create groups and decide what they grant and who belongs to them."),
]

def getPagesForUser(user) -> list[dict[str,str]]:
    pagesForUser = [
        x.getOptionDict()
        for x in PAGES
        if x.permission is None or user.has_perm(x.permission)
    ]
    return pagesForUser


@login_required
def index(request):
    options = getPagesForUser(request.user)
    sections = []
    for category in CATEGORIES:
        pages = [option for option in options if option["category"] == category]
        if pages:
            sections.append({"title": category, "pages": pages})

    # Surface anything waiting on this user's approval right on the landing page
    pendingRequests = AccessRequests.objects.filter(
        status=AccessRequests.Status.REQUESTED
    ).exclude(requester=request.user)
    pendingReviewCount = sum(
        1 for accessRequest in pendingRequests if accessRequest.canBeReviewedBy(request.user)
    )

    return render(request, "tools/home.html", {
        "sections": sections,
        "pendingReviewCount": pendingReviewCount,
    })
