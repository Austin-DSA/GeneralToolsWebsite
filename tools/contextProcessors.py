"""Template context processors for the tools app."""


def navigation(request):
    """Permission-filtered domain links for the masthead, on every page.

    Reuses the home page's DOMAINS/PAGES so the nav, the home cards, and the
    domain landing pages can never drift apart — adding a PageOption or a
    Domain updates all three.
    """
    # Imported here to avoid import-time cycles (views imports models, which
    # must wait for the app registry).
    from . import views

    if not request.user.is_authenticated:
        return {"navDomains": []}

    domainsBySlug = {domain.slug: domain for domain in views.DOMAINS}
    navDomains = []
    for entry in views.getDomainsForUser(request.user):
        entry["active"] = _isActive(domainsBySlug[entry["slug"]], entry["pages"], request.path)
        navDomains.append(entry)
    return {"navDomains": navDomains}


def _isActive(domain, pages, path):
    """A domain is active on its landing page, on any of its tools' pages, and
    on its detail/review subpages (the domain's extraPathPrefixes)."""
    prefixes = [domain.href] + [page["href"] for page in pages] + list(domain.extraPathPrefixes)
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return True
    return False
