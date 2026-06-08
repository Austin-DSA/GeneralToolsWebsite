import dataclasses
import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render, reverse

from tools.utils import sessionDataRequired

from . import permissions

from forms import GuestLoginForm

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class PageOption:
    href : str
    title : str
    permission : str

    def getOptionDict(self):
        return {"href" : self.href, "title": self.title}
    
PAGES = [
    PageOption(href="new-event", title="Create an Event", permission=permissions.PUBLISH_EVENT),
    PageOption(href="new-delegated-event", title="Create Delegated Event Request", permission=permissions.REQUEST_DELEGATED_EVENT),
    PageOption(href="events", title="View Published Events", permission=permissions.VIEW_PUBLISHED_EVENTS),
    PageOption(href="delegated-events", title="View Delegated Events", permission=permissions.VIEW_DELEGATED_EVENTS),
    PageOption(href="/admin/tools/linktree/", title="Manage Link Trees", permission=permissions.MANAGE_LINK_TREE),
    PageOption(href="link-metrics", title="Link Tree Metrics", permission=permissions.VIEW_LINK_METRICS),
]

def getPagesForUser(user) -> list[dict[str,str]]:
    pagesForUser = [x.getOptionDict() for x in PAGES if user.has_perm(x.permission)]
    return pagesForUser


@dataclasses.dataclass
class PageOption:
    href: str
    title: str
    permission: str

    def getOptionDict(self):
        return {"href": self.href, "title": self.title}


PAGES = [
    PageOption(
        href="new-event", title="Create an Event", permission=permissions.PUBLISH_EVENT
    ),
    PageOption(
        href="new-delegated-event",
        title="Create Delegated Event Request",
        permission=permissions.REQUEST_DELEGATED_EVENT,
    ),
    PageOption(
        href="events",
        title="View Published Events",
        permission=permissions.VIEW_PUBLISHED_EVENTS,
    ),
    PageOption(
        href="delegated-events",
        title="View Delegated Events",
        permission=permissions.VIEW_DELEGATED_EVENTS,
    ),
    PageOption(
        href=reverse("resolution-list"),
        title="View Resolutions",
        permission=permissions.VIEW_RESOLUTIONS
    ),
    PageOption(
        href= reverse("resolution-new"),
        title="New Resolution",
        permission=permissions.CREATE_RESOLUTION
    )
]


def getPagesForUser(user) -> list[dict[str, str]]:
    pagesForUser = [x.getOptionDict() for x in PAGES if user.has_perm(x.permission)]
    return pagesForUser


@login_required
def index(request):
    options = getPagesForUser(request.user)
    return render(request, "tools/home.html", {"options": options})

# Common Guest Views

# /guestLogin
def guestLogin(request):
    if request.method == "POST":
        form = GuestLoginForm(request.POST)
        email = form.getEmail()
        name = form.getName()
        if not form.is_valid() or name is None or email is None:
            logger.error("GuestVoteLogin: Web login form submitted is not valid.")
            return render(request,"tools/common/error.html",{"errorStr":"The form could not be validated. Please ensure you have entered valid information."})
        logger.info("GuestVoteLogin: Recieved Guest web login for %s:%s", email, name)

        request.session["email"] = email
        request.session["name"] = name

        return redirect("guest-dash")
    else:
        return render(request, "tools/common/guest-login.html", {"form": GuestLoginForm()})

# /guestDash
@sessionDataRequired(sessionKeys=["email", "name"], redirectURL="guest-login")
def guestDashBoard(request):
    return render(request, "tools/common/guest-dash.html")
