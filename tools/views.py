import logging
import dataclasses

from django.http import Http404
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from . import permissions
from .models import AccessRequests

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class PageOption:
    href : str
    title : str
    permission : str | None  # None = visible to every logged-in user
    icon : str = ""
    description : str = ""
    category : str = "Tools"
    # Some pages are noise for superusers (e.g. My Access - they implicitly
    # hold every permission, so there's nothing meaningful to show)
    hideForSuperusers : bool = False

    def isVisibleTo(self, user) -> bool:
        if self.hideForSuperusers and user.is_superuser:
            return False
        return self.permission is None or user.has_perm(self.permission)

    def getOptionDict(self):
        return {
            "href" : self.href,
            "title": self.title,
            "icon": self.icon,
            "description": self.description,
            "category": self.category,
        }

@dataclasses.dataclass
class Domain:
    """A top-level area of the site: a card on the home page, a landing page
    listing its tools, and a masthead nav link. PAGES options join to a domain
    via their ``category`` (the domain's title)."""
    slug : str          # URL: the landing page lives at /<slug>
    title : str         # matches PageOption.category
    icon : str
    description : str
    # URL prefixes beyond the slug and the domain's own PAGES hrefs that still
    # belong to it (detail/review pages) - used to highlight the active nav link.
    extraPathPrefixes : tuple = ()

    @property
    def href(self):
        return "/" + self.slug


# Order here is the order of home-page cards and masthead links. A domain only
# renders if the user can see at least one of its tools.
DOMAINS = [
    Domain(slug="events", title="Events", icon="calendar",
           description="Publish chapter events to Zoom, Action Network, and Google Calendar, directly or through delegated review.",
           extraPathPrefixes=("/event/", "/delegated-event/", "/approve-delegated-event/", "/manage-event-owners/")),
    Domain(slug="link-trees", title="Link Trees", icon="link",
           description="The chapter's link pages, QR codes, and click analytics."),
    Domain(slug="access", title="Access", icon="key",
           description="Your groups and permissions, access requests, and member management."),
]

PAGES = [
    PageOption(href="new-event", title="Create an Event", permission=permissions.PUBLISH_EVENT,
               icon="calendar", category="Events",
               description="Publish an event to Zoom, Action Network, and Google Calendar in one go."),
    PageOption(href="new-delegated-event", title="Create Delegated Event Request", permission=permissions.REQUEST_DELEGATED_EVENT,
               icon="send", category="Events",
               description="Draft an event for an owner's authorizers to approve and publish."),
    PageOption(href="published-events", title="View Published Events", permission=permissions.VIEW_PUBLISHED_EVENTS,
               icon="archive", category="Events",
               description="Everything that has been published, with all the links."),
    PageOption(href="delegated-events", title="View Delegated Events", permission=permissions.VIEW_DELEGATED_EVENTS,
               icon="inbox", category="Events",
               description="Track event requests and where they are in review."),
    PageOption(href="manage-event-owners", title="Manage Event Owners", permission=permissions.MANAGE_EVENT_OWNERS,
               icon="users", category="Events",
               description="Owners that delegated requests are filed against, and who can approve them."),
    PageOption(href="manage-link-trees", title="Manage Link Trees", permission=permissions.MANAGE_LINK_TREE,
               icon="link", category="Link Trees",
               description="Edit the chapter's link pages, items, and QR codes."),
    PageOption(href="link-metrics", title="Link Tree Metrics", permission=permissions.VIEW_LINK_METRICS,
               icon="bar-chart", category="Link Trees",
               description="Click and scan analytics for every link tree."),
    PageOption(href="my-access", title="My Access", permission=None,
               icon="user", category="Access", hideForSuperusers=True,
               description="The groups you belong to and the permissions you currently have."),
    PageOption(href="request-access", title="Request Access", permission=None,
               icon="key", category="Access",
               description="Ask to join a group or get a permission. Approvers are emailed a review link."),
    PageOption(href="access-requests", title="View Access Requests", permission=None,
               icon="mail", category="Access",
               description="Your requests and their status, plus any waiting on your review."),
    PageOption(href="manage-access", title="Manage Member Access", permission=permissions.APPROVE_ACCESS_REQUEST,
               icon="user-check", category="Access",
               description="Grant or revoke groups and permissions directly, without a request."),
    PageOption(href="manage-groups", title="Manage Groups", permission=permissions.APPROVE_ACCESS_REQUEST,
               icon="users", category="Access",
               description="Create groups and decide what they grant and who belongs to them."),
]

def getPagesForUser(user) -> list[dict[str,str]]:
    pagesForUser = []
    for x in PAGES:
        if x.isVisibleTo(user):
            option = x.getOptionDict()
            # Hrefs are stored root-relative ("new-event"); links render from
            # "/", "/events", etc., so make them absolute once, here.
            if not option["href"].startswith("/"):
                option["href"] = "/" + option["href"]
            pagesForUser.append(option)
    return pagesForUser


def getDomainsForUser(user) -> list[dict]:
    """The DOMAINS the user can see anything in, with their visible tools."""
    options = getPagesForUser(user)
    domainsForUser = []
    for domain in DOMAINS:
        pages = [option for option in options if option["category"] == domain.title]
        if pages:
            domainsForUser.append({
                "slug": domain.slug,
                "title": domain.title,
                "href": domain.href,
                "icon": domain.icon,
                "description": domain.description,
                "pages": pages,
            })
    return domainsForUser


@login_required
def index(request):
    # Surface anything waiting on this user's approval right on the landing page
    pendingRequests = AccessRequests.objects.filter(
        status=AccessRequests.Status.REQUESTED
    ).exclude(requester=request.user)
    pendingReviewCount = sum(
        1 for accessRequest in pendingRequests if accessRequest.canBeReviewedBy(request.user)
    )

    return render(request, "tools/home.html", {
        "domains": getDomainsForUser(request.user),
        "pendingReviewCount": pendingReviewCount,
    })


@login_required
def domain(request, domainSlug):
    """Landing page for one domain: the tools the user can reach within it."""
    domainInfo = next((x for x in DOMAINS if x.slug == domainSlug), None)
    if domainInfo is None:
        raise Http404(f"No such domain: {domainSlug}")

    options = getPagesForUser(request.user)
    pages = [option for option in options if option["category"] == domainInfo.title]
    return render(request, "tools/domain.html", {
        "domain": domainInfo,
        "pages": pages,
    })
