from django.test import TestCase

from tools.LinkTree import WikiLinkResolver
from tools.WikiAutomation.OutlineAPI import OutlineAPIError

from tools.tests.support import FakeOutline


# --- wiki resolver (fake Outline) ------------------------------------------


class WikiResolverTests(TestCase):
    def test_resolve_latest_picks_newest_title_match(self):
        api = FakeOutline({
            "documents.search": {"data": [
                {"document": {"id": "a", "title": "2026-04-01 GBM Agenda",
                              "publishedAt": "2026-04-01T00:00:00Z", "updatedAt": "2026-04-01T00:00:00Z",
                              "url": "/doc/apr"}},
                {"document": {"id": "b", "title": "2026-05-01 GBM Agenda",
                              "publishedAt": "2026-05-01T00:00:00Z", "updatedAt": "2026-05-02T00:00:00Z",
                              "url": "/doc/may"}},
                {"document": {"id": "c", "title": "Some unrelated note",
                              "publishedAt": "2026-06-01T00:00:00Z", "updatedAt": "2026-06-01T00:00:00Z",
                              "url": "/doc/other"}},
            ]},
            "shares.create": {"data": {"id": "sh-may", "url": "https://wiki.example.org/s/may-share",
                                       "published": True}},
        })
        result = WikiLinkResolver.resolveLatest(api, "GBM Agenda")
        self.assertIsNotNone(result)
        # The winner's PUBLISHED SHARE url (no wiki login), not the direct /doc/ url.
        self.assertEqual(result.url, "https://wiki.example.org/s/may-share")
        self.assertEqual(result.title, "2026-05-01 GBM Agenda")
        self.assertIn(("shares.create", {"documentId": "b"}), api.calls)
        # Already published - no shares.update needed.
        self.assertNotIn("shares.update", [method for method, _ in api.calls])

    def test_resolve_latest_ignores_drafts_and_non_title_matches(self):
        api = FakeOutline({
            "documents.search": {"data": [
                {"document": {"id": "d", "title": "GBM Agenda draft",
                              "publishedAt": None, "updatedAt": "2026-07-01T00:00:00Z", "url": "/doc/d"}},
            ]},
        })
        self.assertIsNone(WikiLinkResolver.resolveLatest(api, "GBM Agenda"))

    def test_resolve_pinned(self):
        api = FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
            "shares.create": {"data": {"id": "sh-x", "url": "https://wiki.example.org/s/onboarding",
                                       "published": True}},
        })
        result = WikiLinkResolver.resolvePinned(api, "x")
        self.assertEqual(result.url, "https://wiki.example.org/s/onboarding")
        self.assertEqual(result.title, "Onboarding")

    def test_resolver_publishes_an_unpublished_share(self):
        # shares.create is get-or-create; an existing-but-unpublished share must
        # be published (shares.update) before its url is usable without login.
        api = FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
            "shares.create": {"data": {"id": "sh-x", "url": "https://wiki.example.org/s/onboarding",
                                       "published": False}},
            "shares.update": {"data": {"id": "sh-x", "published": True}},
        })
        result = WikiLinkResolver.resolvePinned(api, "x")
        self.assertEqual(result.url, "https://wiki.example.org/s/onboarding")
        self.assertIn(("shares.update", {"id": "sh-x", "published": True}), api.calls)

    def test_resolver_skips_share_creation_when_disabled(self):
        # createShares=False (the dry-run path) must be side-effect-free on
        # Outline: no shares.* calls at all, direct URL returned. The fake has
        # no shares.create response, so any attempt would KeyError.
        api = FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
        })
        result = WikiLinkResolver.resolvePinned(api, "x", createShares=False)
        self.assertEqual(result.url, "https://wiki.example.org/doc/onboarding")
        self.assertEqual([method for method, _ in api.calls], ["documents.info"])

    def test_resolver_falls_back_to_direct_url_when_sharing_fails(self):
        # Missing scope / sharing disabled must never kill resolution - the
        # direct /doc/ url is no worse than not sharing at all.
        api = FakeOutline({
            "documents.info": {"data": {"id": "x", "title": "Onboarding",
                                        "publishedAt": "2026-01-01T00:00:00Z", "url": "/doc/onboarding"}},
            "shares.create": OutlineAPIError("shares.create", 403, "missing scope"),
        })
        result = WikiLinkResolver.resolvePinned(api, "x")
        self.assertIsNotNone(result)
        self.assertEqual(result.url, "https://wiki.example.org/doc/onboarding")
