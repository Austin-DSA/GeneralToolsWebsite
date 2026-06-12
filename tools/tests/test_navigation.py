"""The navigation registry wiring: route anchoring, the bounded domain route,
masthead active-state by URL name, and registry-fed breadcrumbs.

These are the parity guards for the refactor that replaced views.PAGES/DOMAINS
path-prefix navigation with tools/navigation.py - every behavior asserted here
was traced from the old _isActive()/catch-all implementation first.
"""
import datetime

from django.test import TestCase
from django.urls import get_resolver, resolve, reverse

import tools.urls
from tools import navigation
from tools.models import DelegatedEvents, EventOwners, LinkTree, PostedEvents
from tools.tests.support import LoginClientMixin, UserFactory, fastHashing

# Routes that are reachable logged-out (or are the home/landing routes) and so
# deliberately have no entry in ROUTE_NAME_TO_DOMAIN_SLUG.
ROUTE_NAMES_WITHOUT_A_DOMAIN = {"index", "link-tree", "link-go", "qr-redirect", "domain"}

# The active domain is marked twice per page: the desktop dropdown summary and
# the mobile menu's domain header link.
ACTIVE_EVENTS_LINK = '<a class="mobile-menu-domain is-active" aria-current="page" href="/events">'
ACTIVE_LINK_TREES_LINK = '<a class="mobile-menu-domain is-active" aria-current="page" href="/link-trees">'
ACTIVE_EVENTS_SUMMARY = '<summary class="main-nav-link is-active" aria-current="true">Events'


class RegistryRouteAnchoringTests(TestCase):
    """Every NavTool/map entry must point at a real route - the loud-failure
    guard that replaced the old silently-dead-tile href strings."""

    def test_every_registered_route_name_reverses(self):
        for tool in navigation.NAV_TOOLS:
            with self.subTest(routeName=tool.routeName):
                reverse(tool.routeName)  # raises NoReverseMatch on a typo

    def test_every_route_in_map_is_a_known_url_name(self):
        knownUrlNames = {
            name for name in get_resolver().reverse_dict if isinstance(name, str)
        }
        for routeName in navigation.ROUTE_NAME_TO_DOMAIN_SLUG:
            with self.subTest(routeName=routeName):
                self.assertIn(routeName, knownUrlNames)

    def test_all_gated_routes_map_to_exactly_one_domain(self):
        # Drift guard: a new gated route added to tools/urls.py must be
        # claimed by a domain or the masthead will never highlight for it.
        gatedRouteNames = {
            pattern.name for pattern in tools.urls.urlpatterns
            if pattern.name not in ROUTE_NAMES_WITHOUT_A_DOMAIN
        }
        self.assertEqual(gatedRouteNames, set(navigation.ROUTE_NAME_TO_DOMAIN_SLUG))
        knownDomainSlugs = {domain.slug for domain in navigation.NAV_DOMAINS}
        for routeName, domainSlug in navigation.ROUTE_NAME_TO_DOMAIN_SLUG.items():
            with self.subTest(routeName=routeName):
                self.assertIn(domainSlug, knownDomainSlugs)

    def test_cancel_stuck_delegated_event_maps_to_events(self):
        # The trap route: its name shares no prefix with its domain, so a
        # glob-built map would have silently dropped it.
        self.assertEqual(
            navigation.ROUTE_NAME_TO_DOMAIN_SLUG["cancel-stuck-delegated-event"], "events"
        )


@fastHashing
class RegistryVisibilityTests(TestCase):
    def test_permission_filters_visible_tools(self):
        publisher = UserFactory.make("publisher", perms=("publishEvent",))
        visibleTitles = [tool.title for tool in navigation.visibleToolsForUser(publisher)]
        self.assertIn("Create an Event", visibleTitles)
        self.assertNotIn("View Published Events", visibleTitles)

    def test_permissionless_user_sees_only_ungated_tools(self):
        nobody = UserFactory.make("nobody")
        self.assertTrue(
            all(tool.permission is None for tool in navigation.visibleToolsForUser(nobody))
        )


class DomainRouteBoundingTests(TestCase):
    """The bounded re_path that replaced the <slug:domainSlug> catch-all."""

    def test_domain_reverse_is_unchanged(self):
        self.assertEqual(reverse("domain", args=["events"]), "/events")
        self.assertEqual(reverse("domain", args=["link-trees"]), "/link-trees")
        self.assertEqual(reverse("domain", args=["access"]), "/access")

    def test_new_root_route_is_not_shadowed(self):
        # Only the known slugs match the domain route now - nothing else on a
        # single segment is absorbed, so route ordering stopped mattering.
        self.assertEqual(resolve("/events").url_name, "domain")
        self.assertEqual(resolve("/events").kwargs, {"domainSlug": "events"})
        self.assertEqual(resolve("/link-trees").kwargs, {"domainSlug": "link-trees"})
        self.assertEqual(resolve("/access").kwargs, {"domainSlug": "access"})
        self.assertEqual(resolve("/admin/").namespace, "admin")

    def test_domain_landing_no_trailing_slash(self):
        # The old <slug:> converter never matched "/access/" - preserve that.
        resp = self.client.get("/access/")
        self.assertEqual(resp.status_code, 404)


@fastHashing
class MastheadActiveStateTests(LoginClientMixin, TestCase):
    """Active-state now keys on the resolved URL name. Each case here
    reproduces highlighting the old path-prefix matching provided."""

    def test_tool_page_highlights_its_domain(self):
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/new-event")
        self.assertContains(resp, ACTIVE_EVENTS_LINK, html=False)
        self.assertContains(resp, ACTIVE_EVENTS_SUMMARY, html=False)

    def test_event_owner_detail_highlights_events(self):
        # Previously covered by the "/manage-event-owners/" extraPathPrefix.
        owner = EventOwners.objects.create(
            name="Test Owner", isPermanent=True,
            expiration=datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC),
        )
        self.loginAs(UserFactory.make("ownerAdmin", perms=("manageEventOwners",)))
        resp = self.client.get(f"/manage-event-owners/{owner.id}")
        self.assertContains(resp, ACTIVE_EVENTS_LINK, html=False)

    def test_event_detail_highlights_events(self):
        # Previously covered by the "/event/" extraPathPrefix (B7).
        event = _makePostedEvent("Posted Test Event")
        self.loginAs(UserFactory.make("viewer", perms=("viewPublishedEventList",)))
        resp = self.client.get(f"/event/{event.pk}/")
        self.assertContains(resp, ACTIVE_EVENTS_LINK, html=False)

    def test_delegated_event_detail_highlights_events(self):
        # Previously covered by the "/delegated-event/" extraPathPrefix (B7).
        delegated = _makeDelegatedEvent("Delegated Test Event")
        self.loginAs(UserFactory.make("viewer", perms=("viewDelegatedEventList",)))
        resp = self.client.get(f"/delegated-event/{delegated.pk}/")
        self.assertContains(resp, ACTIVE_EVENTS_LINK, html=False)

    def test_link_metrics_tree_highlights_link_trees(self):
        # Previously covered by startswith() on the link-metrics tool href.
        tree = LinkTree.objects.create(slug="test-tree", title="Test Tree")
        self.loginAs(UserFactory.make("analyst", perms=("viewLinkMetrics",)))
        resp = self.client.get(f"/link-metrics/{tree.slug}")
        self.assertContains(resp, ACTIVE_LINK_TREES_LINK, html=False)

    def test_index_highlights_no_domain(self):
        # The home page never highlighted a masthead link and still must not.
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/")
        self.assertNotContains(resp, "main-nav-link is-active")
        self.assertNotContains(resp, "mobile-menu-domain is-active")


@fastHashing
class BreadcrumbTests(LoginClientMixin, TestCase):
    """The {% breadcrumbs %} tag renders the same trails the templates used
    to hand-write, including the four drifted shorter-than-tile labels."""

    def test_tool_page_trail_is_registry_derived(self):
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/new-event")
        self.assertContains(resp, '<a href="/events">Events</a>')
        self.assertContains(resp, '<span aria-current="page">Create an Event</span>')

    def test_drifted_breadcrumb_label_is_preserved(self):
        # The trail says "Published Events" even though the tile says
        # "View Published Events" - the breadcrumbLabel field carries it.
        self.loginAs(UserFactory.make("viewer", perms=("viewPublishedEventList",)))
        resp = self.client.get("/published-events")
        self.assertContains(resp, '<span aria-current="page">Published Events</span>')

    def test_detail_page_trail_has_parent_and_dynamic_leaf(self):
        event = _makePostedEvent("Posted Test Event")
        self.loginAs(UserFactory.make("viewer", perms=("viewPublishedEventList",)))
        resp = self.client.get(f"/event/{event.pk}/")
        self.assertContains(resp, '<a href="/published-events">Published Events</a>')
        self.assertContains(resp, '<span aria-current="page">Posted Test Event</span>')

    def test_domain_landing_trail_is_two_crumbs(self):
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/events")
        self.assertContains(resp, '<span aria-current="page">Events</span>')

    def test_home_page_renders_no_trail(self):
        self.loginAs(UserFactory.make("publisher", perms=("publishEvent",)))
        resp = self.client.get("/")
        self.assertNotContains(resp, 'class="breadcrumbs"')


def _makePostedEvent(title) -> PostedEvents:
    eventMoment = datetime.datetime(2030, 1, 1, 18, 0, tzinfo=datetime.UTC)
    return PostedEvents.objects.create(
        title=title, start=eventMoment, end=eventMoment, timezone="America/Chicago",
        locationName="", streetAddress="", city="", state="", zip="", country="",
        description="", instructions="",
        dateCreated=eventMoment, datePublished=eventMoment,
        anManageLink="", anShareLink="", gCalLink="", zoomLink="", zoomAccount="",
        reason="",
    )


def _makeDelegatedEvent(title) -> DelegatedEvents:
    eventMoment = datetime.datetime(2030, 1, 1, 18, 0, tzinfo=datetime.UTC)
    return DelegatedEvents.objects.create(
        title=title, start=eventMoment, end=eventMoment, timezone="America/Chicago",
        locationName="", streetAddress="", city="", state="", zip="", country="",
        description="", instructions="",
        dateCreated=eventMoment,
        status=DelegatedEvents.Status.REQUESTED, reason="",
    )
