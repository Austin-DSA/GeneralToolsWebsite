"""Views for the Membership domain - currently just the retention metrics
page (the "bleeding curve" over MembershipSnapshot rows).

Mirrors linkTreeViews.py: thin views, aggregation in the feature package
(tools/MembershipList/metrics.py), gated on a custom permission from
tools/permissions.py.
"""

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render

from . import permissions
from .MembershipList import metrics


@login_required
@permission_required(permissions.VIEW_MEMBERSHIP_METRICS)
def membership_metrics(request):
    return render(request, "tools/membership_metrics.html", metrics.curveContext())
