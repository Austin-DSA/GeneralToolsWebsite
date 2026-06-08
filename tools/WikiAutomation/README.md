# WikiAutomation — read-only Outline client

A small, pure-stdlib REST client for the Outline wiki (`wiki.austindsa.org`),
used by the **Link Tree** feature to surface published wiki content (e.g. "the
latest GBM agenda") on a link page.

It is deliberately **read-only and Django-free** — no document creation, editing,
or publishing — so it's safe to run under a low-privilege service-account token
and easy to unit-test by stubbing one method.

## What it does

`OutlineAPI` (in `OutlineAPI.py`) wraps Outline's RPC-style API (every call is
`POST {baseUrl}/api/{method}` with a Bearer token). Link Tree uses exactly three
operations:

| Method | Outline endpoint | Used for |
|--------|------------------|----------|
| `searchDocuments(query, collectionId=None)` | `documents.search` | find published docs whose title matches a phrase (the "latest GBM agenda" case) |
| `getDocument(documentId)` | `documents.info` | fetch one pinned document by id |
| `absoluteDocUrl(urlPath, documentId)` | — | build the absolute link the button points at |

Supporting types: `OutlineConfig` (base URL + token), `OutlineDocument` (the
fields Link Tree needs — id, title, published flag, url, recency timestamps),
and `OutlineAPIError` (raised on any non-2xx / transport failure).

`documents.search` returns only **published** documents and nests each hit under
a `document` key; `OutlineDocument.fromApiObject` normalizes that. Results come
back in Outline's relevance order — callers sort by `recencyKey()` to pick the
newest.

## Who uses it

- `tools/LinkTree/WikiLinkResolver.py` — `resolveLatest()` / `resolvePinned()`
  turn a wiki-backed `LinkTreeItem` into a concrete URL + title.
- `tools/management/commands/sync_link_tree_wiki.py` — the host-scheduled sweep
  that resolves wiki items and caches the result on each item, so the public page
  never calls Outline at request time.

See `tools/LinkTree/README.md` for the feature-level picture.

## Token (optional, read-only service account)

Wiki surfacing needs a token with the **`documents.search`** scope, which the LC
secretary token deliberately does **not** have — so this uses a separate
read-only token, ideally on a **service/bot account**:

```
documents.search documents.info
```

The token is **optional**: without it the app still boots and
`sync_link_tree_wiki` simply skips (wiki-backed items stay unresolved and
hidden — never a dead button). To enable surfacing, configure it via
`SecretManager.getOutlineReadConfig()`:

- Prod: add `OutlineBaseUrl` + `OutlineReadApiToken` to
  `tools/SecretManager/secrets.json` (both optional — absent them, boot is not
  blocked; both must be present to enable surfacing).
- Dev: set `OutlineReadApiToken()` in `tools/SecretManager/devSecrets.py`.

## Testing

`OutlineAPI` makes all network calls through one private method, `_call`. Tests
subclass it and stub `_call` with canned envelopes — no network, no mocking
library (see `_FakeOutline` in `tools/tests.py`). Run `python manage.py test tools`.

## Layout

- `OutlineAPI.py` — the pure stdlib client (`OutlineAPI`, `OutlineConfig`,
  `OutlineDocument`, `OutlineAPIError`). No Django imports.
