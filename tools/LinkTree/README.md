# Link Tree

A self-hosted replacement for the chapter's third-party "linktree" pages. It
gives Austin DSA full control plus two things the hosted services don't: **usage
tracking** on every link and QR code, and **QR codes that route through this
site** so a printed code stays repointable and every scan is counted.

## What it does

- **Public link pages** at `/t/<slug>/` — a clean, mobile-first list of buttons
  (like Linktree). A tree is either `PUBLIC` (anyone) or `MEMBERS` (login
  required). You can run several trees (e.g. a public external one and a
  members-only internal one).
- **Tracked redirects.** Buttons link to `/go/<item_id>/` and QR codes encode
  `/qr/<code>/`. Both log an event, then 302 to the real destination. Nothing
  on the public page links straight to the destination, so all usage is counted.
- **Repointable QR codes.** The QR image encodes the site URL, not the target.
  Change a code's target in the admin and every already-printed code now points
  somewhere new — no reprint, and the scan history carries across.
- **Wiki-surfaced links.** A link item can auto-resolve to Outline wiki content
  (e.g. "the latest GBM agenda") — see *Wiki items* below.
- **Metrics dashboard** at `/link-metrics` — per-tree clicks vs scans, a 30-day
  trend, top links, per-QR/per-campaign scan counts, and a CSV export.

## Maintaining it (Django admin)

Day-to-day editing is the **Django admin** — no code, and it inherits Django's
group-based permissions. The quickest way to grant a maintainer everything is
the seeded **"Link Tree Maintainers"** group:

```bash
python manage.py seed_link_tree_groups   # idempotent; re-syncs the group's perms
```

Then in `/admin/`: add the user to that group **and** tick `is_staff` on their
record. The `is_staff` flag is required for admin access and a group cannot set
it (see *RBAC* below for why the group bundles two different permission kinds).

- **Link Trees** → add a tree, set its slug/visibility, and add **items inline**.
  Order with the `order` number (lower = higher on the page). Each item can have
  an emoji icon, a label, and a subtitle.
- **QR Codes** → create a code, point it at a tree / item / raw URL (exactly
  one), and use the **Download QR image** links (SVG for print, PNG for the web).
  The admin shows the scan URL the code encodes.

## RBAC

Two **custom** permissions are registered on the shared `PermissionRights`
model (`tools/permissions.py`), following the event-permission pattern:

| Permission | Codename | Gates |
|---|---|---|
| `MANAGE_LINK_TREE` | `manageLinkTree` | the QR **image** view (`/qr/<code>/image`) |
| `VIEW_LINK_METRICS` | `viewLinkMetrics` | the metrics dashboard + CSV |

Note the split: those custom permissions only gate the two **views** above.
Creating and editing the actual `LinkTree` / `LinkTreeItem` / `QRCode` **records**
happens in the admin, whose `ModelAdmin`s use Django's **standard per-model**
`add/change/delete/view` permissions (plus `is_staff`) — not `manageLinkTree`.
So a full maintainer needs both kinds, which is exactly what the
`seed_link_tree_groups` command bundles into one group.

The public pages and the scan/click redirects require **no** permission (a
members-only tree only requires being logged in).

## Wiki items ("surface the latest GBM agenda")

A `LinkTreeItem` of kind **Wiki** resolves to an Outline document:

- **Latest matching** — newest *published* doc whose title contains a phrase
  (e.g. `GBM Agenda`), optionally scoped to a collection.
- **Pinned** — one specific document id.

Resolution is **out-of-band**: the `sync_link_tree_wiki` management command
resolves each wiki item and caches the URL + title on the item. The public page
only reads that cache, so it never calls Outline at request time and keeps
working if Outline is down. An unresolved wiki item is simply hidden (never a
dead button).

```bash
python manage.py sync_link_tree_wiki [--dry-run] [--quiet]
```

Schedule it with the host scheduler (there is no in-process scheduler). A daily
run is plenty. Example cron (8am CT-ish):

```cron
0 13 * * *  cd /path/to/deploy && docker compose exec -T tools-site python manage.py sync_link_tree_wiki >> /var/log/link_tree.log 2>&1
```

### Outline read token (separate service account)

Surfacing *published* docs needs the `documents.search` scope, which the LC
secretary token deliberately does **not** have. So this uses a dedicated
read-only token, ideally on a **service/bot account**. The token is **optional** —
without it the app still runs and `sync_link_tree_wiki` just skips (WIKI items
stay unresolved and hidden). To turn wiki surfacing on:

- Config: `SecretManager.getOutlineReadConfig()` →
  `OutlineBaseUrl()` + `OutlineReadApiToken()` (returns `None` when unconfigured).
- Prod: add `OutlineBaseUrl` and `OutlineReadApiToken` to
  `tools/SecretManager/secrets.json` (optional — both must be present to enable
  surfacing, but their absence no longer blocks boot).
- Dev: set `OutlineReadApiToken()` in `tools/SecretManager/devSecrets.py`.
- Required token scopes: `documents.search documents.info` (add
  `collections.documents` only if you scope items to a collection).

## Privacy

Austin DSA is privacy-conscious, so tracking is **aggregate, not surveillant**:

- **No raw IP is ever stored.** Each event keeps a `visitorHash` — a salted,
  daily-rotating digest of IP+user-agent, useful only for rough *same-day*
  unique counts and useless as a cross-day identifier.
- Only coarse `uaFamily` (e.g. `mobile-safari`) and the referrer **host** are
  kept — never the full referrer URL.
- `destinationUrl` is snapshotted on each event so metrics survive later edits.

Events are append-only (`LinkEvent`); prune old rows on whatever retention
schedule the chapter prefers.

## Layout

- `../models.py` — `LinkTree`, `LinkTreeItem`, `QRCode`, `LinkEvent`.
- `tracking.py` — privacy-first event helpers (`visitorHash`, `uaFamily`,
  `referrerHost`) + the exception-safe `recordEvent` writer.
- `WikiLinkResolver.py` — Django-free `resolveLatest` / `resolvePinned` over the
  Outline client (unit-tested by overriding `OutlineAPI._call`).
- `../linkTreeViews.py` — public pages + tracked redirects + QR image + metrics.
- `../management/commands/sync_link_tree_wiki.py` — the wiki resolver sweep.
- `../management/commands/seed_link_trees.py` — builds the `links` (public) and
  `members` (internal) trees from the real Linktree content.
- `../management/commands/seed_qr_codes.py` — mints the standing QR codes (e.g.
  the `become-a-member` code targeting the public tree's "Join Austin DSA!"
  item). Run after `seed_link_trees`.
- `../management/commands/seed_link_tree_groups.py` — creates the "Link Tree
  Maintainers" group (full link-tree permission set).
- `../templates/linktree/tree.html` — the public page.
- `../templates/tools/link_metrics.html` — the dashboard.

Tests live in `tools/tests.py` (`python manage.py test tools`).
