"""Template context processors for the tools app."""


def navigation(request):
    """Permission-filtered nav sections for the masthead, on every page.

    Reuses the home page's PAGES/CATEGORIES so the nav and the home menu can
    never drift apart — adding a PageOption updates both.
    """
    # Imported here to avoid import-time cycles (views imports models, which
    # must wait for the app registry).
    from . import views

    if not request.user.is_authenticated:
        return {"navSections": []}

    options = views.getPagesForUser(request.user)
    # PAGES hrefs are root-relative names like "new-event" (the home page links
    # them from "/"); the nav renders on every URL, so make them absolute.
    for option in options:
        if not option["href"].startswith("/"):
            option["href"] = "/" + option["href"]

    sections = []
    for category in views.CATEGORIES:
        pages = [option for option in options if option["category"] == category]
        if pages:
            sections.append({"title": category, "pages": pages})
    return {"navSections": sections}
