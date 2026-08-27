# Calibre-Web Flibusta Integration — Design Spec

Date: 2026-08-27
Status: Approved (design), pending implementation plan

## Goal

Add a native "Download Books" section to a self-hosted Calibre-Web instance that lets
an authorized user search Flibusta, pick a format, and add the book straight into the
Calibre library — using Calibre-Web's own import pipeline, not a bolt-on.

## Constraints & Context

- Calibre-Web runs from the official `lscr.io/linuxserver/calibre-web` Docker image.
- The server reaches Flibusta directly (no Tor/proxy required), but the Flibusta base
  URL and an optional proxy must be configurable.
- Flibusta access logic uses the existing `flibusta-api` (TypeScript/Node) library,
  wrapped in a small Node sidecar service. No reimplementation in Python.
- Calibre-Web core is patched minimally (2 files, ~6 lines). All feature logic lives
  in a separate Python package mounted into the image. Patches are pinned to a
  specific Calibre-Web release and stored as `.patch` files.
- Adding to the library reuses Calibre-Web's internal upload helpers
  (`file_handling_on_upload`, `create_book_on_upload`, `helper.update_dir_structure`,
  `move_coverfile`) — the same code path as a manual UI upload.

## Architecture

### Components

| Component | Responsibility | Depends on |
|---|---|---|
| `flibusta-sidecar` (Node, own container) | Sole owner of Flibusta/OPDS knowledge. REST API: search, cover proxy, download (with zip unpacking). Stateless. | `flibusta-api`, Express |
| `cps_flibusta` (Python package, mounted into Calibre-Web image at `cps/flibusta/`) | Flask blueprint: search page, proxy to sidecar, add-to-library via Calibre-Web internals, dedup, permission checks. | `requests`, `cps.editbooks`, `cps.db`, `cps.helper` |
| Core patches (`patches/*.patch`) | Register blueprint in `cps/main.py`; add nav menu item in `cps/templates/layout.html`. Pinned to a Calibre-Web release. | — |
| `Dockerfile` + `docker-compose.yml` | Build custom Calibre-Web image on top of the official one; run sidecar in the same compose network. | — |

Isolation principle: Calibre-Web never knows the Flibusta URL or OPDS format; the
sidecar never knows about the Calibre database. They communicate over the narrow HTTP
contract below. Blocking/mirror-switching/`fb2+zip` unpacking all stay in the sidecar.

### Sidecar HTTP contract

Base URL inside the compose network: `http://flibusta-sidecar:8080`. Not published externally.

**`GET /health`** -> `200 {"status":"ok"}`

**`GET /search?q=<str>&page=<int>`** ->
```jsonc
{
  "results": [
    {
      "flibustaId": 416925,
      "title": "...",
      "authors": [{"name": "...", "flibustaId": 5778}],
      "series": "...",              // nullable
      "categories": ["...", "..."],
      "description": "<p>...</p>",   // HTML from OPDS content
      "coverUrl": "/cover/416925",   // relative to the sidecar; nullable
      "formats": [
        {"format": "fb2",  "mime": "application/fb2+zip"},
        {"format": "epub", "mime": "application/epub"},
        {"format": "mobi", "mime": "application/x-mobipocket-ebook"}
      ]
    }
  ],
  "page": 0,
  "hasNext": true
}
```
Source: `getBooksByNameFromOpdsPaginated`. `formats` map `downloads[].type` (MIME) to a
short format name (`application/fb2+zip` -> `fb2`, `application/x-mobipocket-ebook` -> `mobi`,
`application/epub` -> `epub`, `application/pdf` -> `pdf`, `application/txt+zip` -> `txt`,
`application/rtf+zip` -> `rtf`, `application/html+zip` -> `html`, `application/djvu` -> `djvu`,
`application/msword` -> `doc`). Unknown MIME types are dropped from `formats`.

**`GET /cover/<flibustaId>`** -> streams the cover image (sidecar fetches Flibusta `/i/...`).
`404` if no cover. Exists so the browser and Calibre-Web never hit Flibusta directly
(mixed-content, privacy).

**`GET /download/<flibustaId>/<format>`** ->
- Sidecar downloads `/b/<id>/<format>` from Flibusta.
- If the response is a zip containing a single book file (`fb2+zip`, zipped `epub`, etc.),
  it unpacks and returns the bare file.
- Response: file body + real `Content-Type` + `Content-Disposition: attachment; filename="<transliterated-title>.<ext>"`.
- Errors (JSON body `{"error": "..."}`): `404` (book/format missing), `422` (corrupt/empty
  file), `502` (Flibusta unreachable), `504` (timeout).

Sidecar config via env: `FLIBUSTA_BASE_URL` (default `http://flibusta.is/`),
`FLIBUSTA_PROXY` (optional), `PORT` (default 8080), request timeout, retry count (1-2),
User-Agent string.

### Blueprint routes (`cps_flibusta`)

Registered as `Blueprint("flibusta", __name__, url_prefix="/flibusta", template_folder="templates")`.
Every route: `@login_required` + upload-permission check (same getter the stock `/upload`
route uses, `current_user.role_upload()` and `config.config_uploading`).

| Route | Method | Behavior |
|---|---|---|
| `/flibusta` (endpoint `flibusta.index`) | GET | Render search page (extends `layout.html`), empty result list. |
| `/flibusta/search` | GET (AJAX/JSON) | Proxy to sidecar `/search`. For each result add `alreadyInLibrary: bool` and `bookId` (dedup). Rewrite `coverUrl` -> `/flibusta/cover/<id>`. |
| `/flibusta/cover/<id>` | GET | Stream-proxy to sidecar `/cover/<id>` with cache headers. |
| `/flibusta/add` | POST (JSON: `flibustaId`, `format`) | Main flow (below). Returns `{status, bookId, url}` or `{error}`. |

### `/flibusta/add` flow

1. Dedup check by identifier `flibusta:<id>`. If present, return
   `{status: "already_exists", bookId, url}`.
2. `GET sidecar /download/<id>/<format>` -> save to a temp file in Calibre-Web's working
   area (`tempfile` within the configured upload dir).
3. Wrap the temp file in a Werkzeug `FileStorage`-compatible object matching what
   `file_handling_on_upload` expects.
4. `file_handling_on_upload(file_storage)` -> `meta` (Calibre-Web extracts embedded metadata).
5. Enrich `meta` with OPDS data that is missing or weaker in the file: authors, series,
   tags (from `categories`), description, `identifiers = {"flibusta": str(id)}`,
   language `ru` (fallback only — do not overwrite a language embedded in the file).
6. `create_book_on_upload(modify_date, meta)` -> `book_id`.
7. `helper.update_dir_structure(book_id, config.get_book_path(), ...)` + `move_coverfile(meta, db_book)`.
8. `calibre_db.session.commit()`. On any exception: `rollback()` + delete temp file +
   remove the created book directory.
9. Response `{status: "added", bookId, url: url_for("web.show_book", book_id=book_id)}`.

Steps 3-7 mirror `editbooks.upload()`; reuse the helpers, do not copy them. Exact helper
names/signatures to be confirmed against the pinned Calibre-Web release during
implementation.

### Sidecar client (`cps_flibusta/sidecar.py`)

Thin module: `search(q, page)`, `cover(flibusta_id)`, `download(flibusta_id, fmt)`.
URL from env `FLIBUSTA_SIDECAR_URL`. Central error handling -> custom exceptions
`SidecarUnavailable`, `BookNotFound`, `UpstreamError`.

## Frontend

One page `templates/flibusta.html`, extends `layout.html` (same header/menu/theme):
- Search input + button. Input -> debounce -> `fetch /flibusta/search`.
- Results as cards in Calibre-Web's existing book-card style: cover, title, author(s),
  series, truncated description.
- Per card: format dropdown (from `formats`) + "Add to library" button.
- Button states: `Add` -> `Loading...` -> `Added (open)` / `Error: ...`.
- `alreadyInLibrary` -> show "Already in library" with a link immediately.
- "Load more" pagination driven by `hasNext`.

Vanilla JS (~150 lines) in `static/`. Calibre-Web uses no SPA framework.

## Error Handling

| Situation | Behavior |
|---|---|
| Sidecar unreachable | Banner "Flibusta service unavailable", buttons disabled. |
| Flibusta blocked/timeout | `502/504` from sidecar -> UI "Flibusta unavailable, try later". |
| Format missing | Format option not shown (list comes from `formats`). |
| Corrupt/empty zip | Sidecar returns `422`; add aborts before any DB write. |
| Calibre import fails | Full rollback: delete temp file and created book dir, return `{error}`. |
| Duplicate | `already_exists` + link, no re-add. |
| No permission | 403; menu item hidden. |

## Testing

**Sidecar (Node, mocha + nock):**
- OPDS response -> `/search` contract (real-response fixtures).
- MIME -> format mapping; link absolutization.
- `/download`: unpack `fb2+zip` to bare `.fb2`; pass through non-zip formats; correct
  `Content-Disposition`.
- Upstream errors -> correct status codes.

**Blueprint (pytest + Flask test client, sidecar mocked via `responses`):**
- `/flibusta/search`: render, `alreadyInLibrary` flag, sidecar-unavailable handling.
- `/flibusta/add`: happy path creates a book in a temp Calibre DB with identifier
  `flibusta:<id>`, series and tags from OPDS.
- Dedup: second add returns `already_exists`.
- Rollback: mocked import failure -> no DB row, no files left.
- Permission: without `role_upload` -> 403.

**Integration smoke (manual/CI):** `docker compose up`, real search for a fixture query,
add one book, verify it appears in the library.

## Repository Layout (proposed)

```
integration/calibre-web-flibusta/
  sidecar/            # Node service
    server.js
    src/
    test/
    Dockerfile
    package.json
  cps_flibusta/       # Python package mounted into the Calibre-Web image
    __init__.py
    views.py
    sidecar.py
    metadata.py       # OPDS -> Calibre meta enrichment
    templates/flibusta.html
    static/flibusta.js
    static/flibusta.css
  patches/
    0001-register-flibusta-blueprint.patch
    0002-nav-menu-item.patch
  tests/
    test_views.py
    test_add_flow.py
    conftest.py
  Dockerfile          # FROM lscr.io/linuxserver/calibre-web:<pinned>
  docker-compose.yml
  README.md
```

Whether this lives in the `flibusta-api` repo or a separate repo is an open packaging
question; default is a new top-level `integration/` directory here.

## Open Questions / Risks

- Exact Calibre-Web release to pin, and the precise signatures of
  `file_handling_on_upload` / `create_book_on_upload` in that release.
- linuxserver image layout: confirm app path (`/app/calibre-web`) and that `pip install`
  in a derived image persists (s6 overlay does not wipe site-packages).
- Cover handling: `move_coverfile` expects a cover extracted by the uploader; when the
  book file has no embedded cover, fetch the OPDS cover in the blueprint and place it
  where `move_coverfile` looks, or set it via the edit-book path afterwards.
- Filename transliteration for `Content-Disposition` with Cyrillic titles.

## Effort Estimate

~3-4 days: sidecar (~0.5-1d), blueprint + add pipeline (~1.5d), frontend (~0.5d),
Docker/patches/compose (~0.5d), tests alongside via TDD.
