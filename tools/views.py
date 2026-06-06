import logging

from django.http import Http404
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

from . import navigation
from .models import AccessRequests

logger = logging.getLogger(__name__)


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
        "domains": navigation.visibleDomainsForUser(request.user),
        "pendingReviewCount": pendingReviewCount,
    })


@login_required
def domain(request, domainSlug):
    """Landing page for one domain: the tools the user can reach within it."""
    domainInfo = navigation.findDomainBySlug(domainSlug)
    if domainInfo is None:
        raise Http404(f"No such domain: {domainSlug}")

    return render(request, "tools/domain.html", {
        "domain": domainInfo,
        "pages": navigation.visibleToolLinksForDomain(domainSlug, request.user),
    })
