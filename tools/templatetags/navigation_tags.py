"""The {% breadcrumbs %} tag: one registry-fed implementation of the site's
Home > Domain > Page trail, replacing the per-template hand-written copies."""

from django import template
from django.urls import reverse

register = template.Library()


@register.inclusion_tag("tools/common/breadcrumbs.html", takes_context=True)
def breadcrumbs(context, parentCrumbs=None, currentLabel=None,
                parentLabel=None, parentUrl=None):
    """Render the breadcrumb trail for the current request.

    With no arguments, the whole trail is derived from the navigation
    registry: the resolved URL name finds the owning domain and (for pages
    that have a tile) the leaf label. Pages the registry cannot fully
    describe pass explicit pieces:

    - ``currentLabel``: the leaf text, when the page has no tile or the
      leaf is dynamic (e.g. ``currentLabel=tree.title``).
    - ``parentLabel`` + ``parentUrl``: one intermediate crumb between the
      domain and the leaf. The url is an already-resolved string - build it
      with ``{% url '...' as theUrl %}`` in the template.
    - ``parentCrumbs``: a list of ``{"label": str, "url": str}`` dicts from
      the view context, for trails with several intermediate crumbs or
      crumbs whose reverse() needs runtime arguments.

    Pages outside the registry (home, login, error pages) render no trail.
    """
    resolverMatch = getattr(context.get("request"), "resolver_match", None)
    urlName = resolverMatch.url_name if resolverMatch else None
    if urlName is None:
        return {"breadcrumbTrail": []}

    # Imported here (not at module top) so the template engine can load this
    # tag library before the app registry is fully ready.
    from tools import navigation

    # The domain landing pages are the one 2-crumb trail: Home > Domain.
    if urlName == "domain":
        domain = navigation.findDomainBySlug(resolverMatch.kwargs.get("domainSlug"))
        if domain is None:
            return {"breadcrumbTrail": []}
        return {"breadcrumbTrail": [
            {"label": "Home", "url": reverse("index")},
            {"label": domain.title, "url": None},
        ]}

    domain = navigation.domainForRouteName(urlName)
    tool = navigation.toolForRouteName(urlName)

    intermediateCrumbs = list(parentCrumbs) if parentCrumbs else []
    if parentLabel and parentUrl:
        intermediateCrumbs.append({"label": parentLabel, "url": parentUrl})

    leafLabel = currentLabel or (tool.trailLabel if tool else None)
    if domain is None or leafLabel is None:
        # Not a registry page (or a sub-route that passed nothing): no trail.
        return {"breadcrumbTrail": []}

    breadcrumbTrail = [
        {"label": "Home", "url": reverse("index")},
        {"label": domain.title, "url": reverse("domain", args=[domain.slug])},
    ]
    breadcrumbTrail.extend(intermediateCrumbs)
    breadcrumbTrail.append({"label": leafLabel, "url": None})
    return {"breadcrumbTrail": breadcrumbTrail}
