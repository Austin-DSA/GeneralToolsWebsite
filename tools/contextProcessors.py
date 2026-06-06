"""Template context processors for the tools app."""


def navigation(request):
    """Permission-filtered domain links for the masthead, on every page.

    Reuses the navigation registry (tools/navigation.py) so the nav, the home
    cards, and the domain landing pages can never drift apart - adding a
    NavTool or a NavDomain updates all three.
    """
    # Imported here to avoid import-time cycles (the registry imports
    # permissions, whose model must wait for the app registry).
    from . import navigation as registry

    if not request.user.is_authenticated:
        return {"navDomains": []}

    # Active-state comes from the resolved URL name (which domain owns this
    # route), not from path-prefix matching - see ROUTE_NAME_TO_DOMAIN_SLUG.
    activeDomainSlug = registry.activeDomainSlugForRequest(request)
    navDomains = []
    for entry in registry.visibleDomainsForUser(request.user):
        entry["isActiveNavDomain"] = entry["slug"] == activeDomainSlug
        navDomains.append(entry)
    return {"navDomains": navDomains}
