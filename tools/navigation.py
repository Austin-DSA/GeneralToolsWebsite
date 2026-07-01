"""The site's navigation registry: the single source of truth for domains,
tools, and which routes belong to which domain.

Everything navigational derives from here:
- the home page's domain cards and each domain landing page's tool tiles
  (``visibleDomainsForUser`` / ``visibleToolLinksForDomain``),
- the masthead active-state (``activeDomainSlugForRequest`` via
  ``ROUTE_NAME_TO_DOMAIN_SLUG``),
- the breadcrumb trail (the ``{% breadcrumbs %}`` tag in
  ``templatetags/navigation_tags.py`` via ``toolForRouteName`` /
  ``domainForRouteName``),
- the bounded /<domain> landing route (``tools/urls.py`` builds its regex
  from ``NAV_DOMAINS``).

Tools are anchored on their Django URL name (``routeName``), never on a
hand-maintained path string - hrefs are resolved with ``reverse()`` at
render time, so a registry entry pointing at a missing route fails loudly
(see tests/test_navigation.py) instead of rendering a dead tile.
"""

import dataclasses

from . import permissions


@dataclasses.dataclass
class NavTool:
    routeName : str     # Django URL name in tools/urls.py; resolved via reverse()
    title : str
    permission : str | None  # None = visible to every logged-in user
    icon : str = ""
    description : str = ""
    domainSlug : str = ""     # joins the tool to its NavDomain
    # The shorter label breadcrumb trails use where it differs from the tile
    # title (e.g. "Published Events" vs "View Published Events"). Empty means
    # the trail uses ``title`` as-is.
    breadcrumbLabel : str = ""
    # Some pages are noise for superusers (e.g. My Access - they implicitly
    # hold every permission, so there's nothing meaningful to show)
    hideForSuperusers : bool = False

    def isVisibleTo(self, user) -> bool:
        if self.hideForSuperusers and user.is_superuser:
            return False
        return self.permission is None or user.has_perm(self.permission)

    @property
    def trailLabel(self) -> str:
        """The label breadcrumb trails render for this tool."""
        return self.breadcrumbLabel or self.title


@dataclasses.dataclass
class NavDomain:
    """A top-level area of the site: a card on the home page, a landing page
    listing its tools, and a masthead nav link. NAV_TOOLS entries join to a
    domain via their ``domainSlug``."""
    slug : str          # URL: the landing page lives at /<slug>
    title : str
    icon : str
    description : str

    @property
    def href(self):
        return "/" + self.slug


# Order here is the order of home-page cards and masthead links. A domain only
# renders if the user can see at least one of its tools.
NAV_DOMAINS = [
    NavDomain(slug="events", title="Events", icon="calendar",
              description="Publish chapter events to Zoom, Action Network, and Google Calendar, directly or through delegated review."),
    NavDomain(slug="link-trees", title="Link Trees", icon="link",
              description="The chapter's link pages, QR codes, and click analytics."),
    NavDomain(slug="access", title="Access", icon="key",
              description="Your groups and permissions, access requests, and member management."),
]

# Order within a domain is the order of its landing-page tiles.
NAV_TOOLS = [
    NavTool(routeName="new-event", title="Create an Event", permission=permissions.PUBLISH_EVENT,
            icon="calendar", domainSlug="events",
            description="Publish an event to Zoom, Action Network, and Google Calendar in one go."),
    NavTool(routeName="new-delegated-event", title="Create Delegated Event Request", permission=permissions.REQUEST_DELEGATED_EVENT,
            icon="send", domainSlug="events",
            description="Draft an event for an owner's authorizers to approve and publish."),
    NavTool(routeName="event-list", title="View Published Events", permission=permissions.VIEW_PUBLISHED_EVENTS,
            icon="archive", domainSlug="events", breadcrumbLabel="Published Events",
            description="Everything that has been published, with all the links."),
    NavTool(routeName="delegated-event-list", title="View Delegated Events", permission=permissions.VIEW_DELEGATED_EVENTS,
            icon="inbox", domainSlug="events", breadcrumbLabel="Delegated Events",
            description="Track event requests and where they are in review."),
    NavTool(routeName="manage-event-owners", title="Manage Event Owners", permission=permissions.MANAGE_EVENT_OWNERS,
            icon="users", domainSlug="events",
            description="Owners that delegated requests are filed against, and who can approve them."),
    NavTool(routeName="manage-link-tree-new", title="New Link Tree", permission=permissions.MANAGE_LINK_TREE,
            icon="plus", domainSlug="link-trees",
            description="Start a new link page for the chapter."),
    NavTool(routeName="manage-link-tree-list", title="Manage Link Trees", permission=permissions.MANAGE_LINK_TREE,
            icon="link", domainSlug="link-trees",
            description="Edit the chapter's link pages, items, and QR codes."),
    NavTool(routeName="manage-qr-code-list", title="Manage QR Codes", permission=permissions.MANAGE_LINK_TREE,
            icon="qr-code", domainSlug="link-trees", breadcrumbLabel="QR Codes",
            description="Printable codes that stay repointable after printing, with every scan tracked."),
    NavTool(routeName="link-metrics", title="Link Tree Metrics", permission=permissions.VIEW_LINK_METRICS,
            icon="bar-chart", domainSlug="link-trees",
            description="Click and scan analytics for every link tree."),
    NavTool(routeName="my-access", title="My Access", permission=None,
            icon="user", domainSlug="access", hideForSuperusers=True,
            description="The groups you belong to and the permissions you currently have."),
    NavTool(routeName="request-access", title="Request Access", permission=None,
            icon="key", domainSlug="access",
            description="Ask to join a group or get a permission. Approvers are emailed a review link."),
    NavTool(routeName="access-request-list", title="View Access Requests", permission=None,
            icon="mail", domainSlug="access", breadcrumbLabel="Access Requests",
            description="Your requests and their status, plus any waiting on your review."),
    NavTool(routeName="manage-access", title="Manage Member Access", permission=permissions.APPROVE_ACCESS_REQUEST,
            icon="user-check", domainSlug="access",
            description="Grant or revoke groups and permissions directly, without a request."),
    NavTool(routeName="manage-groups", title="Manage Groups", permission=permissions.APPROVE_ACCESS_REQUEST,
            icon="users", domainSlug="access",
            description="Create groups and decide what they grant and who belongs to them."),
]

# Which domain owns each gated route, by URL name - this is what lights up the
# masthead link on detail/review/sub pages that have no tile of their own.
# It replaces the old path-prefix matching (Domain.extraPathPrefixes plus
# startswith() on tool hrefs), which was trailing-slash-sensitive and silently
# missed routes. Every non-public gated URL name in tools/urls.py must appear
# here exactly once; tests/test_navigation.py enforces that. Public routes
# (index, link-tree, link-go, qr-redirect) and the "domain" landing route
# (resolved from its kwarg instead) are deliberately absent.
ROUTE_NAME_TO_DOMAIN_SLUG = {
    # Events: tools
    "new-event": "events",
    "new-delegated-event": "events",
    "event-list": "events",
    "delegated-event-list": "events",
    "manage-event-owners": "events",
    # Events: detail/review/sub pages
    "approve-delegated-event": "events",
    "event-detail": "events",
    "delegated-event-detail": "events",
    "manage-event-owner": "events",
    "create-event-owner": "events",
    "manage-event-owner-authorizer-search": "events",  # fragment endpoint - active-state only, no breadcrumbs
    "cancel-stuck-delegated-event": "events",
    "publish-status": "events",
    "publish-status-json": "events",       # JSON poll endpoint - mapped for completeness
    "publish-publish-anyway": "events",    # POST-only action - mapped for completeness
    # Link Trees: tools
    "manage-link-tree-new": "link-trees",
    "manage-link-tree-list": "link-trees",
    "manage-qr-code-list": "link-trees",
    "link-metrics": "link-trees",
    # Link Trees: detail/sub pages
    "manage-link-tree-edit": "link-trees",
    "manage-link-tree-item-reorder": "link-trees",
    "manage-link-tree-item-new": "link-trees",
    "manage-link-tree-item-edit": "link-trees",
    "manage-qr-code-new": "link-trees",
    "manage-qr-code-edit": "link-trees",
    "link-metrics-tree": "link-trees",
    "link-metrics-csv": "link-trees",   # non-HTML response - mapped for completeness
    "qr-image": "link-trees",           # non-HTML response - mapped for completeness
    # Access: tools
    "my-access": "access",
    "request-access": "access",
    "access-request-list": "access",
    "manage-access": "access",
    "manage-groups": "access",
    # Access: detail/review/sub pages
    "review-access-request": "access",
    "manage-access-user": "access",
    "manage-group": "access",
    "manage-group-member-search": "access",  # fragment endpoint - active-state only, no breadcrumbs
    "manage-group-delete": "access",
}


def visibleToolsForUser(user) -> list[NavTool]:
    return [tool for tool in NAV_TOOLS if tool.isVisibleTo(user)]


def _toolLinkDict(tool: NavTool) -> dict[str, str]:
    """The dict shape the tile/card templates render (href resolved here)."""
    from django.urls import reverse
    return {
        "href": reverse(tool.routeName),
        "title": tool.title,
        "icon": tool.icon,
        "description": tool.description,
    }


def visibleToolLinksForDomain(domainSlug, user) -> list[dict[str, str]]:
    """The tile dicts for one domain's landing page."""
    return [_toolLinkDict(tool) for tool in visibleToolsForUser(user)
            if tool.domainSlug == domainSlug]


def visibleDomainsForUser(user) -> list[dict]:
    """The NAV_DOMAINS the user can see anything in, with their visible tools.

    Feeds the home-page cards and the masthead. A domain only appears if the
    user can see at least one of its tools, so a fresh account with no
    permissions sees the Access domain only (my-access/request-access are
    permissionless) - same behavior as before the registry existed.
    """
    visibleTools = visibleToolsForUser(user)
    domainsForUser = []
    for domain in NAV_DOMAINS:
        pages = [_toolLinkDict(tool) for tool in visibleTools
                 if tool.domainSlug == domain.slug]
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


def findDomainBySlug(domainSlug: str) -> NavDomain | None:
    return next((domain for domain in NAV_DOMAINS if domain.slug == domainSlug), None)


def toolForRouteName(routeName: str) -> NavTool | None:
    """The NavTool whose routeName matches, or None - sub-routes (detail,
    review, outcome pages) have no tile and resolve to None by design."""
    return next((tool for tool in NAV_TOOLS if tool.routeName == routeName), None)


def domainForRouteName(routeName: str) -> NavDomain | None:
    domainSlug = ROUTE_NAME_TO_DOMAIN_SLUG.get(routeName)
    return findDomainBySlug(domainSlug) if domainSlug else None


def activeDomainSlugForRequest(request) -> str | None:
    """Which domain's masthead link should light up for this request.

    Derived from the resolved URL name, not from path prefixes. The "domain"
    landing route serves every domain, so it is the one place the slug comes
    from the resolved kwargs instead of the map.
    """
    resolverMatch = request.resolver_match
    if resolverMatch is None:
        return None
    if resolverMatch.url_name == "domain":
        return resolverMatch.kwargs.get("domainSlug")
    return ROUTE_NAME_TO_DOMAIN_SLUG.get(resolverMatch.url_name)
