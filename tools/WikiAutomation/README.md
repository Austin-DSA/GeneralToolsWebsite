# WikiAutomation: Outline wiki client and automations

A small, pure-stdlib REST client for the Outline wiki (`wiki.austindsa.org`)
plus the automations built on it. The client is Django-free, so it is safe to
run under low-privilege tokens and easy to unit-test by stubbing one method.

Two features share the client, each under its own API token:

| Feature | What it does | Token |
|---------|--------------|-------|
| **Link Tree wiki surfacing** | surfaces published wiki content (e.g. "the latest GBM agenda") on a link page, with public share links | read-only service-account token (`OutlineReadApiToken`, optional) |
| **LC notes auto-publishing** | publishes the secretary's safe LC meeting-note drafts; holds anything that may contain an executive session | the secretary's token (`OutlineApiToken`, mandatory) |

## The client (`OutlineAPI.py`)

`OutlineAPI` wraps Outline's RPC-style API (every call is
`POST {baseUrl}/api/{method}` with a Bearer token, responses come back in an
`{ "ok": bool, "data": ... }` envelope).

| Method | Outline endpoint | Used by | Used for |
|--------|------------------|---------|----------|
| `searchDocuments(query, collectionId=None)` | `documents.search` | Link Tree | find published docs whose title matches a phrase (the "latest GBM agenda" case) |
| `getDocument(documentId)` | `documents.info` | both | fetch one document by id (pinned link items; draft-body fallback) |
| `ensurePublishedShareUrl(documentId)` | `shares.create` + `shares.update` | Link Tree | get-or-create a document's public share link so buttons never gate readers behind a wiki login |
| `listDrafts()` | `documents.drafts` | LC notes | list the token-user's unpublished drafts |
| `publishDocument(documentId)` | `documents.update` | LC notes | publish a clean draft (`{publish: true}`) |
| `getUserEmail(userId)` | `users.info` | LC notes | resolve a draft author's email |
| `absoluteDocUrl(urlPath, documentId)` | (none) | both | build the absolute link for a button or a report/email |

Supporting types: `OutlineConfig` (base URL + token), `OutlineDocument` (id,
title, published flag, url, recency timestamps, author email/id, markdown
body), `OutlineShare`, and `OutlineAPIError` (raised on any non-2xx response
or transport failure).

---

## Link Tree wiki surfacing

Used by the **Link Tree** feature to surface published wiki content on a link
page. This path is read-mostly: no document creation, editing, or publishing.
Its one deliberate write is share-link management, so it runs safely under a
low-privilege service-account token.

### How it works

`documents.search` returns only **published** documents and nests each hit
under a `document` key; `OutlineDocument.fromApiObject` normalizes that.
Results come back in Outline's relevance order, and callers sort by
`recencyKey()` to pick the newest.

`shares.create` is get-or-create in Outline: it returns the existing share
when one already exists. A share only bypasses wiki login once its `published`
flag is set, so `ensurePublishedShareUrl` publishes unpublished shares via
`shares.update`. Anything surfaced on a link tree is by definition meant to be
readable without an account.

### Who uses it

- `tools/LinkTree/WikiLinkResolver.py`: `resolveLatest()` / `resolvePinned()`
  turn a wiki-backed `LinkTreeItem` into a concrete URL + title.
- `tools/management/commands/sync_link_tree_wiki.py`: the host-scheduled sweep
  that resolves wiki items and caches the result on each item, so the public
  page never calls Outline at request time.

See `tools/LinkTree/README.md` for the feature-level picture.

### Token (optional, read-only service account)

Wiki surfacing needs a token with the **`documents.search`** scope, which the
LC secretary token deliberately does **not** have, so this uses a separate
read-only token, ideally on a **service/bot account**:

```
documents.search documents.info shares.create shares.update
```

The token is **optional**: without it the app still boots and
`sync_link_tree_wiki` simply skips (wiki-backed items stay unresolved and
hidden, never a dead button). To enable surfacing, configure it via
`SecretManager.getOutlineReadConfig()`:

- Prod: add `OutlineBaseUrl` + `OutlineReadApiToken` to
  `tools/SecretManager/secrets.json` (both optional; absent them, boot is not
  blocked, and both must be present to enable surfacing).
- Dev: set `OutlineReadApiToken()` in `tools/SecretManager/devSecrets.py`.

---

## LC notes auto-publishing

Auto-publishes Leadership Committee meeting notes from the Outline wiki. The
LC secretary writes notes as **drafts** and often forgets to publish them;
this sweep publishes the safe ones and flags the rest.

### How it works (business logic)

`python manage.py publish_lc_notes` runs **as the note author's Outline user**
and processes that user's **unpublished drafts**. Each draft passes through
two gates:

**Gate 1: Is this an LC note? (title match)**
The draft **title** is matched against the LC-notes pattern (default
case-insensitive regex `lc minutes`; override with `LCNotesTitlePattern`). LC
minutes follow the `"<YYYY-MM-DD> LC Minutes"` naming convention. A draft
whose title does **not** match is **ignored completely**: not published, not
held, not touched.

**Gate 2: Might this contain an executive session? (keyword scan)**
For a draft that passed Gate 1, scan its title + body for executive-session
keywords (`DEFAULT_KEYWORDS`; override with `LCExecKeywords`),
case-insensitive substring match. Outline has no structural marker for a
closed session, so this is a deliberately **high-recall** text scan.

- **No keyword hit: publish** the draft (`documents.update {publish: true}`).
- **Any keyword hit: hold** it (do **not** publish) and email the author that
  it needs manual review. Re-email at most weekly until it's resolved.

A draft is published **only if it clears both gates**: title matches AND zero
keyword hits.

### Safety invariant: confidential minutes are never auto-published

Executive-session / confidential content must never be auto-published. The
design enforces this **two independent ways**:

1. **By title (Gate 1).** Closed-session minutes are kept in a *separate*
   draft titled outside the `lc minutes` convention (e.g.
   `"Executive Session ..."`). Those never match Gate 1, so they never enter
   the sweep at all.
2. **By content (Gate 2).** If a confidential draft *were* titled like a
   normal LC note, its body is full of closed-session discussion, so Gate 2's
   keyword scan holds it.

So a confidential draft is protected whether it's titled differently
(invisible to the sweep) or titled like a normal note (held, not published).
It is never on the publish path.

### Posture: conservative on purpose

A **false positive** (holding a note that was actually fine) just costs a
human a manual publish, which is cheap. A **false negative** (publishing
something it shouldn't) is the dangerous direction. So the keyword list is
high-recall: when in doubt, hold. Run with `--dry-run` for the first week and
review the report before trusting it unattended.

### Idempotent and non-spammy

Publishing a draft removes it from the draft list, so re-runs naturally skip
finished work. Held notes stay drafts, so the `NotifiedHeldNote` model records
them to avoid re-emailing the author on every run (first detection, then
weekly).

Every run prints a stdout report and, unless `--no-email`, emails a run
summary to the configured fallback address.

### Why notes are matched by title, not collection

Outline **drafts have no collection** (a document isn't filed into a
collection until it's published) and the drafts endpoint ignores a
`collectionId` filter. There is therefore no way to scope the sweep "to the LC
collection"; the draft's **title** is the only available selector. That is why
identification is a title pattern (Gate 1), not a collection id.

### Run as the note author (single-token visibility)

In Outline a draft is **private to its author**: there is no API (even for an
admin or owner) that lists another user's drafts. A token only ever sees
drafts created by the user who minted it. **Mint and run this under the
account that authors the LC minutes** (the secretary). If LC minutes ever have
more than one author, the others' drafts are invisible to a single token; that
multi-author case is out of scope (it would need per-author tokens).

### Outline instance specifics (wiki.austindsa.org)

This self-hosted instance is older than the current Outline API spec; the
client is written to its actual behavior:

- Drafts are listed via **`documents.drafts`**: `documents.list` omits drafts
  entirely, and the spec's `statusFilter` param returns **HTTP 500**.
- The drafts response does **not** embed `createdBy.email`, so the author
  email is resolved with a follow-up `users.info` call (falling back to the
  configured fallback address).

### Minimum API token scopes (secretary's token)

Mint the token in Outline under **Settings > API Tokens**, signed in **as the
secretary** (the account that authors the LC Minutes drafts; drafts are
private to their author, so no other account, not even an admin/owner, can see
them).

Grant **exactly these four method scopes**, space-separated when creating the
token:

```
documents.drafts documents.info documents.update users.info
```

| Scope | Used for | Code path |
|-------|----------|-----------|
| `documents.drafts` | list the secretary's unpublished drafts | `OutlineAPI.listDrafts()` |
| `documents.info`   | fetch a draft's body when it isn't embedded in the list response | `OutlineAPI.getDocument()` |
| `documents.update` | publish a clean draft (`{publish: true}`) | `OutlineAPI.publishDocument()` |
| `users.info`       | resolve the author's email (the drafts response omits `createdBy.email`) | `OutlineAPI.getUserEmail()` |

**Deliberately NOT required** (do not grant them):

- `documents.list`: not used by the command (and on this instance it doesn't
  even return drafts).
- `collections.*`: notes are matched by title, not collection.
- `documents.search`: unused by this feature (the Link Tree uses it under its
  own read-only token).
- `documents.create` / `documents.delete`: the token can only publish existing
  drafts; it can never create or delete documents.

No admin/owner role is needed: a normal member account that authors the notes,
with the four scopes above, is sufficient. The token can read and modify only
what that user could, so treat it like their password and revoke from the same
**Settings > API Tokens** page if it leaks.

### Configuration (secrets)

Add to `tools/SecretManager/secrets.json` (prod). `OutlineBaseUrl` is shared
with the Link Tree sync; `OutlineApiToken` and `LCNotesFallbackEmail` are
mandatory `Keys` entries validated at import:

```json
"OutlineBaseUrl": "https://wiki.austindsa.org",
"OutlineApiToken": "<secretary's Outline API token>",
"LCNotesFallbackEmail": "<email used when an author email can't be resolved, and for the run summary>"
```

Optional overrides (omit to use the built-in defaults):

```json
"LCExecKeywords": ["executive session", "closed session", "in camera"],
"LCNotesTitlePattern": "lc minutes"
```

For local dev, `tools/SecretManager/devSecrets.py` ships matching stub
functions so the app boots under `DEBUG=True`.

> Because `settings.py` imports `SecretManager` at load time and `fileSecrets`
> validates every mandatory `Keys` entry, `OutlineApiToken` and
> `LCNotesFallbackEmail` must be present in `secrets.json` or **no**
> management command will run in production. Run `python manage.py migrate`
> after deploy to create the `NotifiedHeldNote` table.

### Flags

- `--dry-run`: classify + report only; publishes nothing, sends no email.
- `--no-email`: publish/hold normally but skip all notifications.
- `--quiet`: suppress per-document lines (summary still prints).

### Held-note re-notification

Held drafts stay drafts, so they'd otherwise be re-found every run. The
`NotifiedHeldNote` model records what's been flagged: the author is emailed on
**first detection**, then at most once per week (`REMINDER_INTERVAL_DAYS`).
Once a note is published by hand it drops out of the draft sweep and the row
becomes inert (no cleanup required).

### Scheduling

There is no in-process scheduler; use the host's scheduler.

**Linux cron** (prod runs Docker; exec inside the container). Central Time
observes DST, so pin to a UTC time that lands in the morning year-round:
`13:00 UTC` = 8:00 AM CDT / 7:00 AM CST:

```cron
0 13 * * *  cd /path/to/deploy && docker compose exec -T tools-site python manage.py publish_lc_notes >> /var/log/lc_notes.log 2>&1
```

**Windows Task Scheduler** (from a box with the venv):

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\path\to\venv\Scripts\python.exe" `
           -Argument "manage.py publish_lc_notes" -WorkingDirectory "C:\path\to\GeneralToolsWebsite"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00AM
Register-ScheduledTask -TaskName "ATXDSA Publish LC Notes" -Action $action -Trigger $trigger `
           -Description "Auto-publish safe LC drafts; hold exec-session notes"
```

Start with `--dry-run` in the schedule, then drop it once the reports look
right.

### Exit codes

`0` on a clean sweep (held notes are expected). Non-zero if the listing call
fails (nothing swept) or any individual document errors, so the scheduler
surfaces real breakage.

---

## Testing

`OutlineAPI` makes all network calls through one private method, `_call`.
Tests subclass it and stub `_call` with canned envelopes: no network, and the
driver tests use a fake api object (see `_FakeOutline` and `FakeOutlineAPI` in
`tools/tests.py`). Run `python manage.py test tools`.

## Layout

- `OutlineAPI.py`: the pure stdlib client (`OutlineAPI`, `OutlineConfig`,
  `OutlineDocument`, `OutlineShare`, `OutlineAPIError`). No Django imports.
- `LCNotePublisher.py`: exception-safe `sweep()` driver + title filter
  (`matchesNotePattern`) + keyword scan (`findExecSessionHits`); returns a
  `SweepResult`. Django-free; takes injected `notifier` /
  `isFirstNotification`.
- `../LinkTree/WikiLinkResolver.py`: turns wiki-backed link items into
  concrete URLs (uses the search/share methods).
- `../management/commands/sync_link_tree_wiki.py`: the Link Tree sweep.
- `../management/commands/publish_lc_notes.py`: wires secrets, the
  `NotifiedHeldNote` model, and email to the LC driver; renders the report.
