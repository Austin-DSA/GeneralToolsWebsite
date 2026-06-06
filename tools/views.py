import logging
import dataclasses

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from . import permissions
import dataclasses

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class PageOption:
    href : str
    title : str
    permission : str | None  # None = visible to every logged-in user

    def getOptionDict(self):
        return {"href" : self.href, "title": self.title}

PAGES = [
    PageOption(href="new-event", title="Create an Event", permission=permissions.PUBLISH_EVENT),
    PageOption(href="new-delegated-event", title="Create Delegated Event Request", permission=permissions.REQUEST_DELEGATED_EVENT),
    PageOption(href="events", title="View Published Events", permission=permissions.VIEW_PUBLISHED_EVENTS),
    PageOption(href="delegated-events", title="View Delegated Events", permission=permissions.VIEW_DELEGATED_EVENTS),
    PageOption(href="/admin/tools/linktree/", title="Manage Link Trees", permission=permissions.MANAGE_LINK_TREE),
    PageOption(href="link-metrics", title="Link Tree Metrics", permission=permissions.VIEW_LINK_METRICS),
    PageOption(href="request-access", title="Request Access (Groups & Permissions)", permission=None),
    PageOption(href="access-requests", title="View Access Requests", permission=None),
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
    return render(request, "tools/home.html", {"options" : options})