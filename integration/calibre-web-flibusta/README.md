# Calibre-Web ↔ Flibusta integration

Add books from [Flibusta](http://flibusta.is) to a self-hosted
[Calibre-Web](https://github.com/janeczku/calibre-web) library from a search page
inside Calibre-Web itself. Search Flibusta, tick one or more formats on a result card,
and the book is downloaded, unpacked, and imported into your Calibre library through
Calibre-Web's own upload pipeline (metadata, cover, identifier, dir structure).
The first ticked format creates (or locates) the book; every further ticked format is
attached to that same book exactly like Calibre-Web's "Upload Format" button.

## Architecture

```
                    docker compose network
  ┌─────────────────────────────────────────────────────────────┐
  │                                                             │
  │  ┌───────────────────────────┐      ┌─────────────────────┐  │
  │  │ calibre-web  (:8083)      │      │ flibusta-sidecar    │  │
  │  │                           │      │  (:8080, internal)  │  │
  │  │  cps/  (pinned 0.6.27)    │      │                     │  │
  │  │   + cps/flibusta/  ◄──────┼──────┼─ Node/Express wrap  │  │
  │  │     (blueprint)   HTTP    │      │  around `flibusta`  │  │
  │  │     /flibusta/index       │      │  npm package        │  │
  │  │     /flibusta/search      │      │  /health /search    │  │
  │  │     /flibusta/cover/<id>  │      │  /cover  /download  │  │
  │  │     /flibusta/add  ───────┼─┐    │                     │  │
  │  │                           │ │    └──────────┬──────────┘  │
  │  │  reuses editbooks.py:     │ │               │ OPDS/HTTP   │
  │  │   file_handling_on_upload │ │               ▼             │
  │  │   create_book_on_upload   │ │        flibusta.is (public) │
  │  │   update_dir_structure    │ │                             │
  │  │   move_coverfile          │ │                             │
  │  └───────────┬───────────────┘ │                             │
  │              │ writes          │ POST fb2/epub bytes         │
  │              ▼                 │ + OPDS metadata             │
  │        /books  (Calibre library, bind mount)                 │
  │         metadata.db + <author>/<title>/…                     │
  └─────────────────────────────────────────────────────────────┘
```

- **Sidecar** (`sidecar/`) — a small Node/Express service wrapping the `flibusta`
  npm package. Normalizes OPDS results to a stable JSON contract, unpacks
  `application/fb2+zip` to raw `.fb2`, passes `epub` through, and ASCII-sanitizes the
  `Content-Disposition` filename (substitution, not transliteration — see
  `asciiFilename` in `sidecar/src/routes.js`). Not exposed on the host — only the
  calibre-web container talks to it.
- **Blueprint** (`cps_flibusta/`, deployed as `cps/flibusta/`) — Flask blueprint
  registered into Calibre-Web via patch `0001`. Renders a search page in the
  Calibre-Web theme, proxies search/cover through the sidecar, and on *add* feeds the
  downloaded bytes into Calibre-Web's stock upload helpers. Dedup and the OPDS
  metadata enrichment key on a `flibusta:<id>` identifier.
- **Two pinned core patches** (`patches/`) — `cps/main.py` (blueprint registration)
  and `cps/templates/layout.html` (a "Download Books" nav item). Generated against the
  exact pinned digest; see "Updating the pinned Calibre-Web release" below.

## Prerequisites

- Docker + Docker Compose v2 (`docker compose`, not `docker-compose`).
- A **pre-seeded Calibre library**. The linuxserver Calibre-Web image ships **no
  `calibredb`**, and Calibre-Web never creates a library on its own — a valid
  `metadata.db` must already exist at `/books` inside the container
  (`./data/books` on the host). Options:
  - Copy an existing Calibre library folder into `./data/books` (must contain
    `metadata.db`).
  - Create one with desktop [Calibre](https://calibre-ebook.com/) /
    `calibredb --with-library ./data/books add_empty`.
  - Use Calibre-Web's **Admin → Database Configuration** ("Add new books database
    location") to point at a folder that already has a `metadata.db`. See the
    [Calibre-Web wiki](https://github.com/janeczku/calibre-web/wiki).
  - If you hand-build the schema, `books.series_index` **must** have `REAL` affinity
    (with `VARCHAR` the stock book-edit page 500s in `cps/jinjia.py:formatfloat`).

## Running

```bash
cd integration/calibre-web-flibusta
docker compose build
docker compose up -d
# http://localhost:8083
```

`./data/config` and `./data/books` are git-ignored bind mounts.

## First run

1. Log in as `admin` / `admin123` and change the password.
2. **Admin → Database Configuration** — point it at `/books`. The folder must already
   contain a valid `metadata.db` (see Prerequisites); Calibre-Web will not create one.
3. **Admin → Basic Configuration → Uploading** — tick *Enable Uploads* and make sure
   `fb2` (and `epub`) are in the allowed upload formats.
4. **Verify the upload toggle persisted.** Re-open Admin → Basic Configuration →
   Uploading and confirm *Enable Uploads* is still ticked (in one scripted run
   `POST /admin/ajaxconfig` did not persist `config_uploading` to `app.db`; toggling
   it in the UI works normally). Without it, `/flibusta/` returns **403** — the
   blueprint gates on `config.config_uploading` and `current_user.role_upload()`.
5. "Download Books" now appears in the top navbar. Search, pick a format, add.

## The `flibusta:<id>` identifier convention

Every imported book gets a Calibre identifier `flibusta:<flibustaId>` (visible on the
book's admin page as `identifier-val-flibusta`). It is the dedup key: re-searching a
book already in the library reports `alreadyInLibrary=true` with its `bookId`, and a
repeat `/flibusta/add` returns `{"status":"already_exists"}` instead of a duplicate.
The OPDS metadata enrichment is fill-only-if-empty and also keyed on this identifier.

## Running the tests locally

**Blueprint (Python):** deps are pinned in `requirements-dev.txt`. The suite stubs
`cps.*` (see `tests/_cps_stubs.py`), so Calibre-Web itself is not required.

```bash
cd integration/calibre-web-flibusta
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q      # ~63 passing
```

**Sidecar (Node):**

```bash
cd integration/calibre-web-flibusta/sidecar
npm install
npm test                                  # ~19 passing (mocha + nock)
```

CI runs both on any change under `integration/calibre-web-flibusta/**`
(`.github/workflows/calibre-web-flibusta.yml`).

## Updating the pinned Calibre-Web release

The two core patches carry context lines from a specific Calibre-Web release, so they
must be regenerated whenever the pinned digest changes.

1. **Pick the new digest.**
   ```bash
   docker pull lscr.io/linuxserver/calibre-web:latest
   docker inspect --format='{{index .RepoDigests 0}}' lscr.io/linuxserver/calibre-web:latest
   ```
   Update the `FROM …@sha256:…` line in `Dockerfile` and §1 of this README (also note
   the human version from `cps/constants.py` → `STABLE_VERSION`).
2. **Re-extract the two upstream files** from the new image:
   ```bash
   IMG=lscr.io/linuxserver/calibre-web@sha256:<new>
   docker run --rm --entrypoint sh "$IMG" -c 'cat /app/calibre-web/cps/main.py' > /tmp/main.py
   docker run --rm --entrypoint sh "$IMG" -c 'cat /app/calibre-web/cps/templates/layout.html' > /tmp/layout.html
   ```
3. **Re-apply the edits by hand** (blueprint import + `register_blueprint` in
   `main.py`; the "Download Books" `<li>` inside the
   `{% if current_user.role_upload() and g.allow_upload %}` guard in `layout.html`)
   and **regenerate both patches** with `diff -u` original vs edited, saving to
   `patches/0001-register-flibusta-blueprint.patch` and
   `patches/0002-nav-menu-item.patch`.
4. **Re-check the upstream signatures** used by `cps_flibusta/` (`file_handling_on_upload`,
   `create_book_on_upload`, `update_dir_structure`, `move_coverfile`, `BookMeta` field
   order). Update §3–§9 of this README if anything changed. Do NOT change `cps_flibusta/`
   here unless a signature actually moved (that is its own task).
5. **Re-run the suites** — `pytest tests -q` and `sidecar && npm test`.
6. **Re-run the in-container smoke** (see "Smoke test results" below) against freshly
   built containers.
7. **Rebuild** — `docker compose build --no-cache && docker compose up -d`.

---

# Calibre-Web — pinned release & upload internals

Task 6 (RESEARCH/PIN). All facts below were extracted from the actual pinned image on
2026-08-27. No feature code here. Phase 2 (Tasks 8/9/10) MUST match this pinned release,
not the design spec.

## 1. Pinned base image

```
lscr.io/linuxserver/calibre-web@sha256:1870b57874a831d7c0c389547826e5be38089c437276299e1646b7c81a497347
```

Obtained via:

```bash
docker pull lscr.io/linuxserver/calibre-web:latest
docker inspect --format='{{index .RepoDigests 0}}' lscr.io/linuxserver/calibre-web:latest
```

Human version: **Calibre-Web `0.6.27`**
(`/app/calibre-web/cps/constants.py` → `STABLE_VERSION = '0.6.27'`).

All Dockerfiles / patches in this integration must pin to the `@sha256:` digest above.

## 2. App path inside the image

`/app/calibre-web` — confirmed:

```bash
docker run --rm --entrypoint sh <img> -c 'ls -d /app/calibre-web'
# /app/calibre-web
```

Blueprint source lives at `/app/calibre-web/cps/`. A mounted package would go at
`/app/calibre-web/cps/flibusta/`.

## 3. VERBATIM source — `cps/editbooks.py`

Blueprint object: `editbook = Blueprint('edit-book', __name__)` — note the endpoint
prefix is **`edit-book`** (hyphen), e.g. `url_for('edit-book.upload')`.

### Decorators used by the stock upload route

```python
def upload_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_upload():
            return f(*args, **kwargs)
        abort(403)

    return inner
```

`login_required_if_no_ano` is imported from `.usermanagement`.

### `upload()` view function

```python
@editbook.route("/upload", methods=["POST"])
@login_required_if_no_ano
@upload_required
def upload():
    if len(request.files.getlist("btn-upload-format")):
        book_id = request.form.get('book_id', -1)
        return do_edit_book(book_id, request.files.getlist("btn-upload-format"))
    elif len(request.files.getlist("btn-upload")):
        for requested_file in request.files.getlist("btn-upload"):
            try:
                modify_date = False
                # create the function for sorting...
                calibre_db.create_functions(config)
                meta, error = file_handling_on_upload(requested_file)
                if error:
                    return error

                db_book, input_authors, title_dir = create_book_on_upload(modify_date, meta)

                # Comments need book id therefore only possible after flush
                modify_date |= edit_book_comments(Markup(meta.description).unescape(), db_book)

                book_id = db_book.id
                title = db_book.title
                if config.config_use_google_drive:
                    helper.upload_new_file_gdrive(book_id,
                                                  input_authors[0],
                                                  title,
                                                  title_dir,
                                                  meta.file_path,
                                                  meta.extension.lower())
                    for file_format in db_book.data:
                        file_format.name = (helper.get_valid_filename(title, chars=42) + ' - '
                                            + helper.get_valid_filename(input_authors[0], chars=42))
                else:
                    error = helper.update_dir_structure(book_id,
                                                        config.get_book_path(),
                                                        input_authors[0],
                                                        meta.file_path,
                                                        title_dir + meta.extension.lower())
                move_coverfile(meta, db_book)
                if modify_date:
                    calibre_db.set_metadata_dirty(book_id)
                # save data to database, reread data
                calibre_db.session.commit()

                if config.config_use_google_drive:
                    gdriveutils.updateGdriveCalibreFromLocal()
                if error:
                    flash(error, category="error")
                link = '<a href="{}">{}</a>'.format(url_for('web.show_book', book_id=book_id), escape(title))
                upload_text = N_("File %(file)s uploaded", file=link)
                WorkerThread.add(current_user.name, TaskUpload(upload_text, escape(title)))
                helper.add_book_to_thumbnail_cache(book_id)

                if len(request.files.getlist("btn-upload")) < 2:
                    if current_user.role_edit() or current_user.role_admin():
                        resp = {"location": url_for('edit-book.show_edit_book', book_id=book_id)}
                        return make_response(jsonify(resp))
                    else:
                        resp = {"location": url_for('web.show_book', book_id=book_id)}
                        return Response(json.dumps(resp), mimetype='application/json')
            except (OperationalError, IntegrityError, StaleDataError) as e:
                calibre_db.session.rollback()
                log.error_or_exception("Database error: {}".format(e))
                flash(_("Oops! Database Error: %(error)s.", error=e.orig if hasattr(e, "orig") else e),
                      category="error")
        return make_response(jsonify(location=url_for("web.index")))
    abort(404)
```

### `create_book_on_upload(modify_date, meta)`

```python
def create_book_on_upload(modify_date, meta):
    title = meta.title
    authr = meta.author
    sort_authors, input_authors, db_author = prepare_authors_on_upload(title, authr)

    title_dir = helper.get_valid_filename(title, chars=96)
    author_dir = helper.get_valid_filename(db_author.name, chars=96)

    # combine path and normalize path from Windows systems
    path = os.path.join(author_dir, title_dir).replace('\\', '/')

    try:
        pubdate = datetime.strptime(meta.pubdate[:10], "%Y-%m-%d")
    except ValueError:
        pubdate = datetime(101, 1, 1)

    # Calibre adds books with utc as timezone
    db_book = db.Books(title, "", sort_authors, datetime.now(timezone.utc), pubdate,
                       '1', datetime.now(timezone.utc), path, meta.cover, db_author, [], "")

    modify_date |= modify_database_object(input_authors, db_book.authors, db.Authors, calibre_db.session,
                                          'author')

    # Add series_index to book
    modify_date |= edit_book_series_index(meta.series_id, db_book)

    # add languages
    invalid = []
    modify_date |= edit_book_languages(meta.languages, db_book, upload_mode=True, invalid=invalid)
    if invalid:
        for lang in invalid:
            flash(_("'%(langname)s' is not a valid language", langname=lang), category="warning")

    # handle tags
    modify_date |= edit_book_tags(meta.tags, db_book)

    # handle publisher
    modify_date |= edit_book_publisher(meta.publisher, db_book)

    # handle series
    modify_date |= edit_book_series(meta.series, db_book)

    # Add file to book
    file_size = os.path.getsize(meta.file_path)
    db_data = db.Data(db_book, meta.extension.upper()[1:], file_size, title_dir)
    db_book.data.append(db_data)
    calibre_db.session.add(db_book)

    # flush content, get db_book.id available
    calibre_db.session.flush()

    # Handle identifiers now that db_book.id is available
    identifier_list = []
    for type_key, type_value in meta.identifiers:
        identifier_list.append(db.Identifiers(type_value, type_key, db_book.id))
    modification, warning = modify_identifiers(identifier_list, db_book.identifiers, calibre_db.session)
    if warning:
        flash(_("Identifiers are not Case Sensitive, Overwriting Old Identifier"), category="warning")
    modify_date |= modification

    return db_book, input_authors, title_dir
```

### `file_handling_on_upload(requested_file)`

```python
def file_handling_on_upload(requested_file):
    # check if file extension is correct
    allowed_extensions = config.config_upload_formats.split(',')
    if requested_file:
        if config.config_check_extensions and allowed_extensions != ['']:
            if not validate_mime_type(requested_file, allowed_extensions):
                flash(_("File type isn't allowed to be uploaded to this server"), category="error")
                return None, make_response(jsonify(location=url_for("web.index")))
    if '.' in requested_file.filename:
        file_ext = requested_file.filename.rsplit('.', 1)[-1].lower()
        if file_ext not in allowed_extensions and '' not in allowed_extensions:
            flash(
                _("File extension '%(ext)s' is not allowed to be uploaded to this server",
                  ext=file_ext), category="error")
            return None, make_response(jsonify(location=url_for("web.index")))
    else:
        flash(_('File to be uploaded must have an extension'), category="error")
        return None, make_response(jsonify(location=url_for("web.index")))

    # extract metadata from file
    try:
        meta = uploader.upload(requested_file,
                               resolve_binary_path(config.config_rarfile_location, SUPPORTED_UNRAR_BINARIES))
    except (IOError, OSError):
        log.error("File %s could not saved to temp dir", requested_file.filename)
        flash(_("File %(filename)s could not saved to temp dir",
                filename=requested_file.filename), category="error")
        return None, make_response(jsonify(location=url_for("web.index")))
    return meta, None
```

### `edit_book_comments(comments, book)` (used in step 6.5 of the add flow)

Extracted verbatim from the pinned image (`sed -n "/def edit_book_comments/,/^def [a-z]/p"
/app/calibre-web/cps/editbooks.py`), 2026-08-27:

```python
def edit_book_comments(comments, book):
    if comments is not None:
        modify_date = False
        if comments:
            comments = clean_string(comments, book.id)
        if len(book.comments):
            if book.comments[0].text != comments:
                book.comments[0].text = comments
                modify_date = True
        else:
            if comments:
                book.comments.append(db.Comments(comment=comments, book=book.id))
                modify_date = True
        return modify_date
```

**It sanitizes.** Every non-empty `comments` value goes through
`clean_string(comments, book.id)` (Calibre-Web's `clean_html` wrapper) *before* it is
persisted, so the blueprint may keep passing the OPDS description through
`Markup(...).unescape()` exactly as stock `upload()` does — the unescaped HTML is
sanitized upstream, on the way into the DB. No stripping is needed on our side.

### `move_coverfile(meta, db_book)` (used in step 7 of the add flow)

```python
def move_coverfile(meta, db_book):
    # move cover to final directory, including book id
    if meta.cover:
        cover_file = meta.cover
    else:
        cover_file = os.path.join(constants.STATIC_DIR, 'generic_cover.jpg')
    new_cover_path = os.path.join(config.get_book_path(), db_book.path)
    try:
        os.makedirs(new_cover_path, exist_ok=True)
        copyfile(cover_file, os.path.join(new_cover_path, "cover.jpg"))
        if meta.cover:
            os.unlink(meta.cover)
    except OSError as e:
        log.error("Failed to move cover file %s: %s", new_cover_path, e)
        flash(_("Failed to Move Cover File %(file)s: %(error)s", file=new_cover_path,
                error=e),
              category="error")
```

### `upload_book_formats(requested_files, book, book_id, no_cover=True)` (backs the "Upload Format" button on the book edit page; used by `_attach_formats`)

```python
def upload_book_formats(requested_files, book, book_id, no_cover=True):
    # Check and handle Uploaded file
    to_save = dict()
    error = False
    allowed_extensions = config.config_upload_formats.split(',')
    for requested_file in requested_files:
        current_filename = requested_file.filename
        if config.config_check_extensions and allowed_extensions != ['']:
            if not validate_mime_type(requested_file, allowed_extensions):
                flash(_("File type isn't allowed to be uploaded to this server"), category="error")
                error = True
                continue
        if current_filename != '':
            if not current_user.role_upload():
                flash(_("User has no rights to upload additional file formats"), category="error")
                error = True
                continue
            if '.' in current_filename:
                file_ext = current_filename.rsplit('.', 1)[-1].lower()
                if file_ext not in allowed_extensions and '' not in allowed_extensions:
                    flash(_("File extension '%(ext)s' is not allowed to be uploaded to this server", ext=file_ext),
                          category="error")
                    error = True
                    continue
            else:
                flash(_('File to be uploaded must have an extension'), category="error")
                error = True
                continue

            file_name = book.path.rsplit('/', 1)[-1]
            filepath = os.path.normpath(os.path.join(config.get_book_path(), book.path))
            saved_filename = os.path.join(filepath, file_name + '.' + file_ext)

            if not os.path.exists(filepath):
                try:
                    os.makedirs(filepath)
                except OSError:
                    flash(_("Failed to create path %(path)s (Permission denied).", path=filepath), category="error")
                    error = True
                    continue
            try:
                requested_file.save(saved_filename)
            except OSError:
                flash(_("Failed to store file %(file)s.", file=saved_filename), category="error")
                error = True
                continue

            file_size = os.path.getsize(saved_filename)

            # Format entry already exists, no need to update the database
            if calibre_db.get_book_format(book_id, file_ext.upper()):
                log.warning('Book format %s already existing', file_ext.upper())
            else:
                try:
                    db_format = db.Data(book_id, file_ext.upper(), file_size, file_name)
                    calibre_db.session.add(db_format)
                    calibre_db.session.commit()
                    calibre_db.create_functions(config)
                except (OperationalError, IntegrityError, StaleDataError) as e:
                    calibre_db.session.rollback()
                    log.error_or_exception("Database error: {}".format(e))
                    flash(_("Oops! Database Error: %(error)s.", error=e.orig if hasattr(e, "orig") else e), category="error")
                    error = True
                    continue

            link = '<a href="{}">{}</a>'.format(url_for('web.show_book', book_id=book.id), escape(book.title))
            upload_text = N_("File format %(ext)s added to %(book)s", ext=file_ext.upper(), book=link)
            WorkerThread.add(current_user.name, TaskUpload(upload_text, escape(book.title)))
            meta = uploader.process(
                saved_filename,
                *os.path.splitext(current_filename),
                rar_executable=resolve_binary_path(config.config_rarfile_location, SUPPORTED_UNRAR_BINARIES),
                no_cover=no_cover)
            merge_metadata(book, meta, to_save)
    return to_save, error
```

Notes for `_attach_formats`:

* takes a `book` ORM object (`calibre_db.get_filtered_book(book_id,
  allow_show_archived=True)`), a list of `FileStorage`, `book_id`, and `no_cover`
  (we pass `book.has_cover`, so an existing cover is not overwritten);
* saves each file as `<bookfolder>/<bookfolder>.<ext>` inside the book's existing
  directory — no `update_dir_structure` / `move_coverfile` needed;
* a format already on the book is logged and skipped, not an error — so the
  blueprint snapshots `get_book_format` *before* the call to tell "already there"
  from "just added";
* returns `(to_save, error)` where `error` is a global "at least one file failed"
  flag that does not say *which* — the per-format verdict comes from re-querying
  `calibre_db.get_book_format(book_id, EXT.upper())` afterwards.

## 4. Import block — top of `cps/editbooks.py`

```python
import os
from datetime import datetime, timezone
import json
from shutil import copyfile

from markupsafe import escape, Markup  # dependency of flask
from functools import wraps

from flask import Blueprint, request, flash, redirect, url_for, abort, jsonify, make_response, Response
from flask_babel import gettext as _
from flask_babel import lazy_gettext as N_
from flask_babel import get_locale
from .cw_login import current_user
from sqlalchemy.exc import OperationalError, IntegrityError, InterfaceError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.expression import func

from . import constants, logger, isoLanguages, gdriveutils, uploader, helper, kobo_sync_status
from .clean_html import clean_string
from . import config, ub, db, calibre_db
from .services.worker import WorkerThread
from .tasks.upload import TaskUpload
from .render_template import render_title_template
from .binary_helper import resolve_binary_path, SUPPORTED_UNRAR_BINARIES
from .kobo_sync_status import change_archived_books
from .redirect import get_redirect_location
from .file_helper import validate_mime_type
from .usermanagement import user_login_required, login_required_if_no_ano
from .string_helper import strip_whitespaces
```

Note: **`current_user` comes from `.cw_login`** (Calibre-Web's vendored login module),
NOT `flask_login`. `flask_login` is not installed in the image (see §8).

## 5. `BookMeta` + `uploader.upload` signature

`BookMeta` is defined in `cps/constants.py` (imported into `uploader.py` as
`from .constants import BookMeta`):

```python
BookMeta = namedtuple('BookMeta', 'file_path, extension, title, author, cover, description, tags, series, '
                                  'series_id, languages, publisher, pubdate, identifiers')
```

Field list (order matters — it's a namedtuple):
`file_path, extension, title, author, cover, description, tags, series, series_id,
languages, publisher, pubdate, identifiers`

Field semantics observed from `uploader.default_meta()` and `create_book_on_upload`:

| Field | Type / meaning |
|---|---|
| `file_path` | absolute path to the saved temp file |
| `extension` | includes leading dot, e.g. `.fb2` (used as `meta.extension.lower()` / `meta.extension.upper()[1:]`) |
| `title` | str |
| `author` | str — a single string (`' & '`-joined upstream); `_('Unknown')` fallback |
| `cover` | path to an extracted cover image file, or `None` |
| `description` | str (HTML); passed through `Markup(meta.description).unescape()` |
| `tags` | str — comma-separated, NOT a list |
| `series` | str |
| `series_id` | str — series index (e.g. `"1"`); `""` default |
| `languages` | str |
| `publisher` | str |
| `pubdate` | str — sliced `meta.pubdate[:10]` parsed as `%Y-%m-%d` |
| `identifiers` | **list of `(key, value)` pairs** — iterated `for type_key, type_value in meta.identifiers`. NOT a dict. Default `[]`. |

`BookMeta` is immutable; upstream enrichment uses `meta = meta._replace(field=...)`.

`uploader.upload(...)` signature:

```python
def upload(uploadfile, rar_excecutable):
    tmp_dir = get_temp_dir()

    filename = uploadfile.filename
    filename_root, file_extension = os.path.splitext(filename)
    md5 = hashlib.md5(filename.encode('utf-8')).hexdigest()  # nosec
    tmp_file_path = os.path.join(tmp_dir, md5)
    log.debug("Temporary file: %s", tmp_file_path)
    uploadfile.save(tmp_file_path)
    return process(tmp_file_path, filename_root, file_extension, rar_excecutable)
```

- `uploadfile` must be a Werkzeug `FileStorage`-like object with `.filename` (str) and
  `.save(path)`.
- `rar_excecutable` (sic) is the resolved unrar binary path; stock caller passes
  `resolve_binary_path(config.config_rarfile_location, SUPPORTED_UNRAR_BINARIES)`.
- Underlying: `def process(tmp_file_path, original_file_name, original_file_extension, rar_executable, no_cover=False)`.

`uploader.py` imports: `os`, `hashlib`, `flask_babel.gettext`, and
`from . import logger, comic, isoLanguages`, `from .constants import BookMeta`,
`from .helper import split_authors`, `from .file_helper import get_temp_dir`,
`from .string_helper import strip_whitespaces`.

## 6. Blueprint registration — `cps/main.py`

`main.py` has a local import block inside `main()` and a run of
`app.register_blueprint(...)` calls. Full relevant section:

```python
def main():
    app = create_app()

    from .web import web
    from .basic import basic
    from .opds import opds
    from .admin import admi
    from .gdrive import gdrive
    from .editbooks import editbook
    from .about import about
    from .search import search
    from .search_metadata import meta
    from .shelf import shelf
    from .tasks_status import tasks
    from .error_handler import init_errorhandler
    from .remotelogin import remotelogin
    try:
        from .kobo import kobo, get_kobo_activated
        from .kobo_auth import kobo_auth
        from flask_limiter.util import get_remote_address
        kobo_available = get_kobo_activated()
    except (ImportError, AttributeError):  # Catch also error for not installed flask-WTF (missing csrf decorator)
        kobo_available = False
        kobo = kobo_auth = get_remote_address = None

    try:
        from .oauth_bb import oauth
        oauth_available = True
    except ImportError:
        oauth_available = False
        oauth = None

    from . import web_server
    init_errorhandler()

    app.register_blueprint(search)
    app.register_blueprint(tasks)
    app.register_blueprint(web)
    app.register_blueprint(basic)
    limiter.limit("3/minute", key_func=request_username)(opds)
    app.register_blueprint(opds)
    app.register_blueprint(jinjia)
    app.register_blueprint(about)
    app.register_blueprint(shelf)
    app.register_blueprint(admi)
    app.register_blueprint(remotelogin)
    app.register_blueprint(meta)
    app.register_blueprint(gdrive)
    app.register_blueprint(editbook)
    if kobo_available:
        limiter.limit("3/minute", key_func=get_remote_address)(kobo)
        app.register_blueprint(kobo)
        app.register_blueprint(kobo_auth)
    if oauth_available:
        app.register_blueprint(oauth)
    success = web_server.start()
    sys.exit(0 if success else 1)
```

Patch `0001` target: add `from .flibusta.views import flibusta` to the import block and
`app.register_blueprint(flibusta)` after the `editbook` registration.

## 7. Navbar `<ul>` — `cps/templates/layout.html`

The Upload item lives in `<ul class="nav navbar-nav navbar-right" id="main-nav">`,
guarded by `{% if current_user.role_upload() and g.allow_upload %}`. Lines ~61–91:

```html
          <ul class="nav navbar-nav navbar-right" id="main-nav">
            {% if current_user.is_authenticated or g.allow_anonymous %}
              {% if g.current_theme == 1 %}
              <li class="dropdown"><a href="#" class="dropdown-toggle profileDrop" ...>
                ...
              </li>
              {% endif %}
              {% if current_user.role_upload() and g.allow_upload %}
                  <li>
                    <form id="form-upload" class="navbar-form" action="{{ url_for('edit-book.upload') }}" data-title="{{_('Uploading...')}}" data-footer="{{_('Close')}}" data-failed="{{_('Error')}}" data-message="{{_('Upload done, processing, please wait...')}}" method="post" enctype="multipart/form-data">
                      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                      <div class="form-group">
                        <span class="btn btn-default btn-file">{{_('Upload')}}<input id="btn-upload" name="btn-upload"
                        type="file" accept="{% for format in accept %}.{% if format != ''%}{{format}}{% else %}*{% endif %}{{ ',' if not loop.last }}{% endfor %}" multiple></span>
                        <input class="hide" id="btn-upload2" name="btn-upload2" type="file" accept="...">
                      </div>
                    </form>
                  </li>
              {% endif %}
              {% if not current_user.is_anonymous and not simple%}
                <li class="top_tasks"><a id="top_tasks" href="{{url_for('tasks.get_tasks_status')}}">...</a></li>
              {% endif %}
              {% if current_user.role_admin() %}
```

Patch `0002`: insert a `<li><a href="{{ url_for('flibusta.index') }}">…</a></li>` inside
the same `{% if current_user.role_upload() and g.allow_upload %}` guard (or a dedicated
`{% if current_user.role_upload() %}` block).

## 8. Which of requests / flask / flask_login are present

| Package | Present | Version |
|---|---|---|
| `requests` | YES | `2.34.2` |
| `flask` | YES | `3.1.3` |
| `flask_login` | **NO** — `ModuleNotFoundError: No module named 'flask_login'` |

Calibre-Web 0.6.27 vendors login as `cps.cw_login` (`from .cw_login import login_required,
current_user`). Blueprint code must import auth helpers from
`cps.usermanagement` / `cps.cw_login`, not `flask_login`.

Also installed and relevant: `SQLAlchemy 2.0.52`, `SQLAlchemy-Utils 0.42.1`,
`flask-babel 4.0.0`, `Flask-WTF 1.3.0` (CSRF), `Flask-Limiter 4.1.1`, `Flask-Dance 7.1.0`,
`APScheduler 3.11.3`, `Wand 0.7.2`.

Check command:

```bash
docker run --rm --entrypoint sh <img> -c \
  'python3 -c "import requests, flask; print(requests.__version__, flask.__version__)"'
```

## 9. Python package layout

- Interpreter: `/lsiopy/bin/python3` → **Python 3.12.3**. It is a venv-like prefix
  (`sys.prefix == /lsiopy`), the linuxserver "lsiopy" environment, first on `PATH`
  (`PATH=/lsiopy/bin:/usr/local/sbin:...`).
- `pip`: `pip 26.2.1 from /lsiopy/lib/python3.12/site-packages/pip (python 3.12)`.
- site-packages: `/lsiopy/lib/python3.12/site-packages` — owned by `root:root`,
  mode `drwxr-xr-x`, and **writable** in a derived image (`touch` succeeded as root).
  So `RUN pip install requests ...` (requests already present, but any extra dep) in a
  `FROM <pinned>` Dockerfile persists — the s6-overlay init (`/etc/s6-overlay/s6-rc.d/…`)
  does not wipe `/lsiopy`.
- App entrypoint script: `/app/calibre-web/cps.py` (`#!/usr/bin/env python`), app package
  `/app/calibre-web/cps/`.
- Container runs as `root` at build/inspect time; at runtime linuxserver drops to the
  PUID/PGID user via s6 (`init-adduser`). A mounted `cps/flibusta/` package needs to be
  readable by that user.

---

## Deltas vs spec

Spec file: `docs/superpowers/specs/2026-08-27-calibre-web-flibusta-design.md`
("/flibusta/add flow" + Architecture). The spec explicitly said signatures were "to be
confirmed against the pinned release". Confirmed differences:

- **Release pinned:** spec left it open ("Exact Calibre-Web release to pin" was an open
  question). Pinned: `0.6.27` at digest `sha256:1870b578…97347`.
- **`file_handling_on_upload` returns a 2-tuple `(meta, error)`**, not just `meta`
  (spec step 4 implies `meta = file_handling_on_upload(file_storage)`). Caller must do
  `meta, error = file_handling_on_upload(fs); if error: return error`.
- **`create_book_on_upload(modify_date, meta)` returns `(db_book, input_authors,
  title_dir)`**, not `book_id` (spec step 6 says "-> `book_id`"). Get the id via
  `db_book.id` after the internal `session.flush()`. Also it takes a `modify_date`
  first arg (bool), not just `meta`.
- **`meta.identifiers` is a list of `(key, value)` pairs, not a dict.** Spec step 5 says
  `identifiers = {"flibusta": str(id)}`. Enrichment must be
  `meta = meta._replace(identifiers=[("flibusta", str(id))])` (list of tuples).
- **`meta.tags` is a comma-separated string, not a list.** Spec step 5 says "tags (from
  `categories`)" implying a list — must be `",".join(categories)`.
- **`meta.author` is a single string**, `' & '`-joined; not a list of authors. `series_id`
  holds the series index (string).
- **`BookMeta` is immutable (namedtuple)** — enrich with `_replace`, cannot assign attrs.
  Full field list: `file_path, extension, title, author, cover, description, tags, series,
  series_id, languages, publisher, pubdate, identifiers`.
- **`meta.extension` includes the leading dot** (`.fb2`). The temp `FileStorage.filename`
  must carry a real extension or `file_handling_on_upload`'s extension check rejects it.
- **Auth import path:** `flask_login` is NOT installed. Spec's blueprint section implies
  `@login_required` from flask_login and mentions `flask_login` as a dep to check. Use
  `from cps.usermanagement import login_required_if_no_ano` and/or
  `from cps.cw_login import login_required, current_user`. The stock `/upload` route uses
  `@login_required_if_no_ano` + a custom `@upload_required` (checks
  `current_user.role_upload()`, else `abort(403)`) — NOT `config.config_uploading`
  directly; the config gate is exposed to templates as `g.allow_upload`.
- **Blueprint endpoint prefix is `edit-book` (hyphenated)**: `url_for('edit-book.upload')`,
  `url_for('edit-book.show_edit_book', ...)`. Spec loosely calls it "the stock `/upload`
  route".
- **`upload()` also calls `edit_book_comments(...)`, `helper.update_dir_structure(...)`,
  `helper.add_book_to_thumbnail_cache(...)`, and enqueues a `WorkerThread` `TaskUpload`.**
  Spec add-flow steps 4–8 omit `edit_book_comments` (needed to persist the description /
  comments after flush) and the thumbnail cache call. `update_dir_structure` signature:
  `helper.update_dir_structure(book_id, config.get_book_path(), input_authors[0],
  meta.file_path, title_dir + meta.extension.lower())`.
- **`move_coverfile(meta, db_book)`** copies `meta.cover` (or the generic cover) to
  `<book_path>/cover.jpg` and `os.unlink`s `meta.cover`. Spec's open question about
  placing an OPDS cover "where `move_coverfile` looks" resolves to: set `meta.cover` to a
  local image path before calling it (it reads `meta.cover` directly, no fixed staging dir).
- **`config.config_uploading`** — the spec's named getter. Actual template guard is
  `g.allow_upload`; permission is `current_user.role_upload()`. Use those.
- **`requests` IS already installed** (`2.34.2`) — no need to add it in the Dockerfile
  (spec listed it as a dep for `cps_flibusta` and something to verify).
- **Python is 3.12.3 at `/lsiopy/bin/python3`**, site-packages writable in a derived
  image — spec's open question ("`pip install` in a derived image persists") is resolved
  YES.
- **`edit_book_comments` sanitizes its input** — non-empty comments are run through
  `clean_string(comments, book.id)` before they are written (verbatim body in section 3).
  The OPDS `description` we hand it is therefore sanitized upstream; the blueprint keeps
  stock `upload()`'s `Markup(meta.description or "").unescape()` and does no stripping of
  its own. (Verified against the pinned digest, 2026-08-27.)
- **OPDS covers are fed in via `meta.cover`** — `_import_into_library` downloads
  `/cover/<id>` from the sidecar into the import temp dir and does
  `meta = meta._replace(cover=<path>)` before `move_coverfile`, which copies it to
  `<book_path>/cover.jpg` and unlinks the temp file. A cover 404 / stream error is
  swallowed (logged at info) and the generic cover is used — a missing cover never fails
  an import. This resolves the "`has_cover` stays 0" smoke-test finding below.
- **`meta.languages` defaults to `"ru"`** — OPDS carries no language element and Flibusta
  is a Russian-language library, so `metadata.enrich` fills a blank `languages` with
  `"ru"` (a string; `edit_book_languages` splits it on commas). A language detected from
  the file itself always wins.
- **Rollback removes the on-disk directory too** — spec step 8 only says "rollback". A
  failure after `create_book_on_upload` can leave `<book_path>/<author>/<title>` behind,
  so the `except` branch also `shutil.rmtree`s the directory resolved from
  `db_book.path`, guarded to paths strictly under `config.get_book_path()`.
- **Multiple formats per add** — `/add` takes `formats` (an ordered JSON list); the
  singular `format` is still accepted and treated as a one-element list. `formats[0]` is
  the primary and goes through `_import_into_library` unchanged; the rest go through
  `_attach_formats`, which calls stock `cps.editbooks.upload_book_formats` (verbatim in
  section 3). The response gained a `formats` object —
  `{"added": [...], "skipped": [...], "failed": [...]}` — so a partial failure (a dead
  download, a rejected file, a format the book already has) is reported per format
  instead of failing the whole request. A dedup hit (`already_exists`) now also runs
  `_attach_formats` for every requested format, so you can add `mobi` to a book that was
  imported as `fb2` earlier. Only a failure of the **primary** format still yields the
  404/502/503/500 responses.

- **`create_book_on_upload` calls `db.Data(db_book, meta.extension.upper()[1:], ...)`** —
  so the DB format string is derived from `meta.extension`; the temp filename's extension
  is load-bearing for the format recorded in Calibre.

---

# Deployment (Task 10)

## Files

| File | Purpose |
|---|---|
| `patches/0001-register-flibusta-blueprint.patch` | `cps/main.py`: `from .flibusta import flibusta` + `app.register_blueprint(flibusta)` |
| `patches/0002-nav-menu-item.patch` | `cps/templates/layout.html`: "Download Books" nav `<li>` after the stock Upload item, inside the same `{% if current_user.role_upload() and g.allow_upload %}` guard |
| `Dockerfile` | pinned Calibre-Web + `cps_flibusta` → `/app/calibre-web/cps/flibusta` + patches |
| `docker-compose.yml` | `calibre-web` (8083) + `flibusta-sidecar` (8080, internal) |

Both patches were generated by diffing the **actual** files extracted from the pinned
digest (`docker run --rm --entrypoint sh <pinned> -c 'cat /app/calibre-web/cps/main.py'`),
so their context lines match the pinned release by construction.

The base image has **no `patch` binary**, so the Dockerfile applies the patches in a
throwaway `alpine` stage (which `apk add patch`) and copies the two patched files into the
final image. That `RUN` starts with `set -e`, so a hunk that no longer applies (e.g. after
a digest bump) **fails the build** instead of silently producing an unpatched image.
`requests` 2.34.2 is already present (§8) — nothing is pip-installed.

### Sidecar environment

| Variable | Default | Notes |
|---|---|---|
| `FLIBUSTA_BASE_URL` | `https://flibusta.is/` | **https by default.** Verified 2026-08-27 against live Flibusta: OPDS search, `/b/416925/fb2` (654 839 bytes) and `/i/25/416925/cover.jpg` all return `200` over https, so no downgrade to http is needed. |
| `FLIBUSTA_PROXY` | *(unset)* | Optional egress proxy (`http://host:port`, credentials in the URL supported). **Honored only when set**; when unset axios runs with `proxy: false`, i.e. env proxies are ignored — the previous behavior. Commented-out placeholder in `docker-compose.yml`. |
| `MAX_DOWNLOAD_BYTES` | `67108864` (64 MB) | Ceiling on book/cover response bytes (`maxContentLength`/`maxBodyLength`). Exceeding it rejects the axios request, which surfaces as `UpstreamError` → **HTTP 502**. |
| `REQUEST_TIMEOUT_MS` | `20000` | Per-request timeout. |
| `RETRY_COUNT` | `1` | Extra attempts on upstream failure. |

`/download/:id/:format` only accepts `fb2, epub, mobi, pdf, txt, rtf, html, djvu, doc`;
anything else is a **400** before any upstream call (the format is interpolated into the
Flibusta URL path). The blueprint's `POST /flibusta/add` applies the matching guard
against `_CONTENT_TYPES` (`fb2, epub, mobi, pdf`).

**axios pin.** `axios` is pinned exactly (`1.20.0`, bumped from `1.2.6` in the final-fix
wave) — this clears every advisory that applied to the `axios.get` surface the sidecar
uses, including the `config.proxy` prototype-pollution MitM gadget that the new
`FLIBUSTA_PROXY` support would otherwise be exposed to. `npm audit` still reports
findings against **transitive** copies (`flibusta` → its own `axios@1.2.6` +
`fast-xml-parser`, used only for OPDS XML, and dev-only `mocha` → `serialize-javascript`,
plus `adm-zip`); clearing those requires `npm audit fix --force`, which downgrades
`flibusta` to `0.4.1` — a breaking change, deliberately not taken.

## Running

```bash
cd integration/calibre-web-flibusta
docker compose build
docker compose up -d
# http://localhost:8083
```

`./data/config` and `./data/books` are bind mounts (git-ignored).

## First run

1. Log in as `admin` / `admin123` and change the password.
2. You are redirected to **Admin → Database Configuration**. Point it at `/books`.
   The folder must already contain a valid Calibre `metadata.db` — Calibre-Web will not
   create one. Either copy an existing Calibre library into `./data/books`, or create one
   with Calibre's `calibredb`. (If you hand-build the schema, `books.series_index` **must**
   have `REAL` affinity — with `VARCHAR` the stock book-edit page 500s inside
   `cps/jinjia.py:formatfloat`.)
3. **Admin → Basic Configuration → Uploading**: tick *Enable Uploads* and make sure `fb2`
   is in the allowed upload formats. Without this, `/flibusta/` returns **403** (the
   blueprint gates on `config.config_uploading` and `current_user.role_upload()`).
4. "Download Books" now appears in the top navbar.

## Smoke test results (2026-08-27, real containers, pinned digest)

| # | Check | Result |
|---|---|---|
| 1 | Calibre-Web starts, no blueprint import error | **PASS** — logs clean; no traceback / `ModuleNotFoundError`; `main.py` shows the two patched lines |
| 2 | Sidecar reachable from the calibre-web container | **PASS** — `curl http://flibusta-sidecar:8080/health` → `{"status":"ok"}` |
| 3 | First-run setup (admin, library at `/books`, uploads on) | **PASS** — done via curl + a hand-seeded `metadata.db` |
| 4 | `GET /flibusta/` as logged-in admin | **PASS** — `200`, HTML contains `#flibusta-search-form`, `#flibusta-q`, `#flibusta-results`; `render_title_template` + `layout.html` render fine (resolves the Task 9 CRITICAL) |
| 5 | `GET /flibusta/search?q=Богатый папа` | **PASS** — `200`, live Flibusta results; `coverUrl` rewritten to `/flibusta/cover/416925` |
| 6 | `POST /flibusta/add` (`flibustaId=416925`, `format=fb2`, `X-CSRFToken`) | **PASS** — `200 {"status":"added","bookId":1,"url":"/book/1"}`. Same request **without** the header → `400` + `csrf.py: The CSRF token is missing.` So CSRF is enforced and the `X-CSRFToken` header satisfies it. fb2 passed `validate_mime_type` / the extension check |
| 7 | Dedup + identifier | **PASS** — repeat search reports `alreadyInLibrary=true, bookId=1`; repeat `/add` → `{"status":"already_exists"}`; `identifiers` row `flibusta:416925`; `/admin/book/1` renders `identifier-val-flibusta = 416925` |
| 8 | `docker compose down` | **PASS** |

A second add using a full search result as `item` (`flibustaId=457363`) produced author
`Роберт Тору Кийосаки`, series `Богатый папа`, series_index `1.0`, comments, the `.fb2`
file and a `cover.jpg` under `/books/<author>/<title> (2)/`.

### Multi-format smoke (2026-08-27, same containers)

| # | Check | Result |
|---|---|---|
| 9 | `POST /add {"flibustaId":178419,"formats":["fb2","mobi"]}` on a **new** book | **PASS** — `200 {"status":"added","bookId":5,"formats":{"added":["fb2","mobi"],"skipped":[],"failed":[]}}`; `data` rows `(5,'FB2'),(5,'MOBI')`; on disk `Крестный отец (5).mobi` (2 836 276 B) next to the `.fb2` and `cover.jpg` |
| 10 | Same request repeated | **PASS** — `already_exists`, `formats.skipped == ["fb2","mobi"]`, no duplicate `data` rows |
| 11 | `formats:["fb2","mobi"]` on a book **already in the library as fb2 only** (`159352` → book 4) | **PASS** — `already_exists`, `formats == {"added":["mobi"],"skipped":["fb2"],"failed":[]}`; `data` gains `(4,'MOBI')`. This is the "Upload Format" path proper |
| 12 | `formats:["fb2","exe"]` | **PASS** — `400 {"error":"Unsupported format: exe"}`, nothing downloaded |

Note: `upload_book_formats` calls `uploader.process(..., no_cover=book.has_cover)`, so on
a book whose `has_cover` is still 0 the extracted cover is left beside the file as
`<bookfolder>.mobi.jpg`. That is stock Calibre-Web behaviour (the book-edit page passes
the same argument), not something the blueprint adds.

### Review fix round (2026-08-27)

| # | Check | Result |
|---|---|---|
| 13 | Import `327641` as `fb2` only, then re-POST `formats:["fb2","mobi"]` | **PASS** — `already_exists`, `{"added":["mobi"],"skipped":["fb2"],"failed":[]}`; only `mobi` reached the sidecar |
| 14 | Same request with the **sidecar container stopped**, both formats now present | **PASS** — `200 {"added":[],"skipped":["fb2","mobi"],"failed":[]}`. A book whose formats are all present needs no sidecar at all — proof that nothing is re-downloaded |
| 15 | Control for 14: `formats:["fb2","epub"]`, sidecar still stopped | **PASS** — `{"skipped":["fb2"],"failed":["epub"]}`; the genuinely missing format is the only one that tries to download |

Book 6's folder holds `Крестный отец - Марио Пьюзо.fb2` (from the import) and
`Крестный отец (6).mobi` (from the attach) — no second `.fb2`, so the present format was
never re-fetched or re-saved.

`/add` also rejects a format the server itself has disabled
(`400 {"error": "Format mobi is not enabled on this Calibre-Web server"}`) by checking
`config.config_upload_formats` up front, instead of letting it fail inside
`upload_book_formats` after a wasted download. An empty/unset setting means "allow all".

### Known issues found while smoking

* **Tag splitting.** `metadata.enrich` joins categories with `","`, and Calibre-Web splits
  `meta.tags` on commas — so a category that itself contains a comma
  (`"Карьера, кадры"`) becomes two tags (`Карьера`, `кадры`). Cosmetic, not a Task 10
  deliverable.
* ~~**`has_cover` stays 0**~~ — **fixed** in the final-fix wave: `_import_into_library`
  now downloads the OPDS cover into the import temp dir and sets `meta.cover` before
  `move_coverfile`, so the real cover lands at `<book_path>/cover.jpg`.
* Calibre-Web's `POST /admin/ajaxconfig` did not persist `config_uploading` to `app.db` in
  this scripted run; the flag was set directly in `/config/app.db` and the container
  restarted. Toggling it in the UI works normally.
