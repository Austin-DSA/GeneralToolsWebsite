"""Seed the two real Austin DSA link trees.

Reproduces the chapter's current third-party Linktree pages so the self-hosted
version is usable out of the box:

  * "links"   (PUBLIC)  ← linktr.ee/austindsa  - events, media, donate, socials
  * "members" (MEMBERS) ← linktr.ee/redtails   - agenda / resolutions / helpful
                                                  links, grouped under headers

Notes on the mapping:
  * The redtails tree's many ``wiki.austindsa.org/s/...`` links are *share* URLs,
    so they're seeded as MANUAL links (their current form). Two WIKI
    "latest matching" items are added to demonstrate auto-surfacing the newest
    GBM / Convention agenda - those resolve once the Outline read token is
    configured and `sync_link_tree_wiki` runs (until then they're hidden, not
    dead).
  * Social accounts are seeded as ordinary links under a "Follow us" header.

Idempotent: re-running updates each tree and rebuilds its items from this
canonical list. It ONLY touches the two slugs below.

    python manage.py seed_link_trees
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from tools.models import LinkTree, LinkTreeItem

H = LinkTreeItem.Kind.SECTION_HEADER
M = LinkTreeItem.Kind.MANUAL
W = LinkTreeItem.Kind.WIKI
LATEST = LinkTreeItem.WikiMode.LATEST_MATCH

# Each item: (kind, icon, label, url_or_query)
EXTERNAL = {
    "slug": "links",
    "title": "Austin DSA",
    "description": "Democratic Socialists of America",
    "visibility": LinkTree.Visibility.PUBLIC,
    "items": [
        (M, "📅", "Upcoming Austin DSA Events", "https://www.austindsa.org/events"),
        (M, "", "Find and contact your City Council member", "https://www.austintexas.gov/council"),
        (M, "📰", "Check out Austin DSA's digital publication, Red Fault!", "https://redfault.com"),
        (M, "🎤", "Watch the Batcast on YouTube!", "https://www.youtube.com/@AustinDSABatcast"),
        (M, "🎤", "Listen to the Batcast on Spotify!", "https://open.spotify.com/show/3MIusSPs7ND3d216lshUM0"),
        (M, "", "Join the Public Power Campaign!", "https://actionnetwork.org/forms/participate-in-the-public-power-campaign"),
        (M, "🌹", "Join Austin DSA!", "https://act.dsausa.org/donate/membership/?source=Austin"),
        (M, "💕", "Donate to Texas Abortion Funds", "https://secure.actblue.com/donate/austindsafundathon"),
        (M, "💸", "Donate to Austin DSA", "https://actionnetwork.org/fundraising/donate-to-austin-dsa"),
        (H, "", "Follow us", ""),
        (M, "", "Facebook", "https://www.facebook.com/atxdsa"),
        (M, "", "Instagram", "https://instagram.com/atxdsa"),
        (M, "", "X / Twitter", "https://x.com/austin_DSA"),
    ],
}

INTERNAL = {
    "slug": "members",
    "title": "Austin DSA - Members",
    "description": "Meeting agendas, resolutions, and member resources.",
    "visibility": LinkTree.Visibility.MEMBERS,
    "items": [
        (H, "", "Agenda", ""),
        # WIKI items: auto-surface the newest matching wiki doc (needs the read
        # token + sync_link_tree_wiki). Hidden until resolved.
        (W, "📋", "Latest GBM Agenda", "GBM Agenda"),
        (W, "🗳️", "Latest Convention Agenda", "Convention Agenda"),
        (M, "", "2026 Austin DSA Convention Agenda", "https://wiki.austindsa.org/s/d67308e3-9707-473a-8af6-1de64ef89f3d"),
        (M, "", "Austin DSA 2026 Convention Sign-in", "https://forms.gle/EqWwg3eyQMr9pZKp8"),
        (M, "", "2026 Democratic Socialists Summit Application Form", "https://form.jotform.com/260916803865162"),
        (M, "", "2026-27 Austin DSA Leadership Committee NOMINATIONS", "https://forms.gle/pVDs8JbfinSnTMUQA"),
        (H, "", "Resolutions", ""),
        (M, "", "R2026-05-01 Your Friendly Neighborhood Socialist", "https://wiki.austindsa.org/s/4dbf5731-a31e-44ab-86b2-a5a1c355238c"),
        (M, "", "R2026-05-02: Continuing Socialists in Office", "https://wiki.austindsa.org/s/549cf9cc-c8c1-418e-a870-6145f189ee54"),
        (M, "", "R2026-05-03: Austin DSA Labor Priority Resolution", "https://wiki.austindsa.org/s/d3bf67e8-972d-4a23-93c2-2f3d8a4220dd"),
        (M, "", "R2026-05-04 - Electoral Resolution", "https://wiki.austindsa.org/s/aa8e2198-5283-4931-bdd6-65ab3ec0395b"),
        (M, "", "R2026-05-05 Budget Resolution for the Austin Afro-Socialist and Socialist of Color Caucus", "https://wiki.austindsa.org/s/c73435df-3321-42db-a97f-56ce498bd6fd"),
        (M, "", "C/B-01: Bringing Austin DSA into Compliance with Unified Grievance Policy", "https://wiki.austindsa.org/s/0723f3e6-4f0f-4ea0-931f-52c4fb92a9dd"),
        (M, "", "C/B-02: For an Expanded L&P Committee Under Leadership of the Vice Chair", "https://wiki.austindsa.org/s/30834610-f469-4e8c-a5f1-342358ef0369"),
        (H, "", "Helpful Links", ""),
        (M, "", "Member handbook", "https://docs.google.com/document/d/1AXxW0qKopSrMcaDLafPlukg4hTAz8-uLo_hdf6vijno/edit?usp=sharing"),
        (M, "", "Robert's Rules", "https://roberts.chicagodsa.org/"),
        (M, "", "Join DSA!", "http://dsausa.org/join"),
        (M, "", "Switch to Solidarity Dues - Give Your 1% for the 99%!", "https://act.dsausa.org/donate/ibd_campaign"),
        (M, "", "Pete Seeger - Solidarity Forever (Lyrics)", "https://wiki.austindsa.org/s/b4b09117-c1ba-44de-8254-c892069dd368"),
        (M, "", "DSA 2024 Program: Workers Deserve More", "http://2024.dsausa.org"),
    ],
}


class Command(BaseCommand):
    help = "Create/refresh the 'links' (public) and 'members' (internal) link trees from the real Linktree content."

    @transaction.atomic
    def handle(self, *args, **options):
        for spec in (EXTERNAL, INTERNAL):
            tree, created = LinkTree.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "title": spec["title"],
                    "description": spec["description"],
                    "visibility": spec["visibility"],
                    "isActive": True,
                },
            )
            # Rebuild items so the seed is the canonical source on re-run.
            tree.items.all().delete()
            objs = []
            for order, (kind, icon, label, target) in enumerate(spec["items"]):
                item = LinkTreeItem(tree=tree, order=order, kind=kind, icon=icon, label=label)
                if kind == W:
                    item.wikiMode = LATEST
                    item.wikiQuery = target
                elif kind == M:
                    item.url = target
                objs.append(item)
            LinkTreeItem.objects.bulk_create(objs)

            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(
                f"{verb} '{tree.slug}' ({tree.get_visibility_display()}) with {len(objs)} items at {tree.getPublicUrl()}"
            ))
