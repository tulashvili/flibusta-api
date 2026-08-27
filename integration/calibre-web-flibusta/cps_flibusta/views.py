"""Flask blueprint that lets Calibre-Web users search Flibusta and import books.

Auth note: Calibre-Web 0.6.27 does NOT ship `flask_login` — it vendors the login
machinery as `cps.cw_login` (see README section 4/8). Import auth helpers from there.
"""

import contextlib
import os
import shutil
import tempfile

from flask import Blueprint, Response, abort, jsonify, request, url_for

from cps import calibre_db, config, logger
from cps.cw_login import current_user, login_required
from cps.render_template import render_title_template

from . import dedup
from . import metadata
from . import sidecar

log = logger.create()

# Declared content types for the FileStorage handed to Calibre-Web's uploader.
# 0.6.27's validate_mime_type() may consult the declared type, so send a real one
# instead of a blanket application/octet-stream.
_CONTENT_TYPES = {
    "fb2": "application/x-fictionbook+xml",
    "epub": "application/epub+zip",
    "mobi": "application/x-mobipocket-ebook",
    "pdf": "application/pdf",
}

flibusta = Blueprint(
    "flibusta",
    __name__,
    url_prefix="/flibusta",
    template_folder="templates",
    static_folder="static",
)


def _require_permission():
    if not config.config_uploading or not current_user.role_upload():
        abort(403)


@flibusta.route("/")
@login_required
def index():
    _require_permission()
    # render_title_template (not flask.render_template): layout.html needs the
    # context Calibre-Web injects here (accept, sidebar, g.*) or it raises.
    return render_title_template("flibusta.html", title="Download Books", page="flibusta")


@flibusta.route("/search")
@login_required
def search():
    _require_permission()
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": [], "page": 0, "hasNext": False})

    try:
        page = int(request.args.get("page", 0) or 0)
    except (TypeError, ValueError):
        page = 0

    try:
        data = sidecar.search(q, page)
    except sidecar.SidecarUnavailable as exc:
        return jsonify({"error": "Flibusta service is unavailable", "detail": str(exc)}), 503
    except sidecar.UpstreamError as exc:
        return jsonify({"error": "Flibusta is unreachable, try again later",
                        "detail": str(exc)}), 502

    for item in data.get("results", []):
        fid = item.get("flibustaId")
        book_id = dedup.find_existing(calibre_db, fid) if fid else None
        item["alreadyInLibrary"] = book_id is not None
        item["bookId"] = book_id
        if item.get("coverUrl") and fid:
            item["coverUrl"] = url_for("flibusta.cover", flibusta_id=fid)
    return jsonify(data)


@flibusta.route("/cover/<int:flibusta_id>")
@login_required
def cover(flibusta_id):
    _require_permission()
    try:
        upstream = sidecar.cover_response(flibusta_id)
    except sidecar.SidecarError:
        abort(404)
    return Response(
        upstream.iter_content(chunk_size=8192),
        content_type=upstream.headers.get("Content-Type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@flibusta.route("/add", methods=["POST"])
@login_required
def add():
    _require_permission()
    payload = request.get_json(silent=True) or {}
    fid = payload.get("flibustaId")
    opds_item = payload.get("item") or {}

    # `formats` (ordered list) is the current contract; the singular `format`
    # stays supported so older clients / scripted callers keep working.
    raw_formats = payload.get("formats")
    if raw_formats is None:
        single = payload.get("format")
        raw_formats = [single] if single else []
    if not isinstance(raw_formats, list):
        return jsonify({"error": "formats must be a list"}), 400
    if fid is None or not raw_formats:
        return jsonify({"error": "flibustaId and format are required"}), 400

    # Whitelist every format: it reaches the sidecar URL and the temp filename's
    # extension, which Calibre-Web turns into the DB format string.
    formats = []
    for raw in raw_formats:
        norm = str(raw or "").lower().lstrip(".")
        if norm not in _CONTENT_TYPES:
            return jsonify({"error": "Unsupported format: {}".format(raw)}), 400
        if norm not in formats:  # dedup, first occurrence wins the order
            formats.append(norm)

    try:
        fid = int(fid)
    except (TypeError, ValueError):
        return jsonify({"error": "flibustaId must be a number"}), 400

    existing = dedup.find_existing(calibre_db, fid)
    title = opds_item.get("title", "")

    tmp_dir = tempfile.mkdtemp(prefix="flibusta_")
    try:
        if existing:
            # Known book: every requested format — including the first — is an
            # "Upload Format" style attach onto the book that is already there.
            entries, failed = _download_formats(fid, formats, title, tmp_dir)
            result = _attach_formats(existing, entries)
            result["failed"] = failed + result["failed"]
            return jsonify({"status": "already_exists", "bookId": existing,
                            "url": url_for("web.show_book", book_id=existing),
                            "formats": _ordered(result, formats)})

        primary = formats[0]
        try:
            content, filename = sidecar.download(fid, primary, title=title)
        except sidecar.BookNotFound:
            return jsonify({"error": "This book/format is no longer available"}), 404
        except sidecar.SidecarUnavailable:
            return jsonify({"error": "Flibusta service is unavailable"}), 503
        except sidecar.UpstreamError:
            return jsonify({"error": "Flibusta is unreachable, try again later"}), 502

        filename = _safe_filename(filename, primary)
        tmp_path = os.path.join(tmp_dir, filename)
        # Secondary downloads happen before the import so a dead secondary is a
        # per-format "failed" entry, never a reason to skip the primary import.
        entries, failed = _download_formats(fid, formats[1:], title, tmp_dir)

        try:
            with open(tmp_path, "wb") as fh:
                fh.write(content)
            book_id = _import_into_library(tmp_path, filename, opds_item, primary, fid)
        except Exception as exc:  # noqa: BLE001 - session already rolled back below
            # Never leak internal paths / SQL / exception text to the client.
            log.error("Flibusta import failed for id %s (%s): %s", fid, primary, str(exc))
            return jsonify({"error": "Import failed"}), 500

        result = _attach_formats(book_id, entries)
        result["added"].append(primary)
        result["failed"] = failed + result["failed"]
        return jsonify({"status": "added", "bookId": book_id,
                        "url": url_for("web.show_book", book_id=book_id),
                        "formats": _ordered(result, formats)})
    finally:
        _safe_rmtree(tmp_dir)


def _ordered(result, formats):
    """Re-emit the added/skipped/failed buckets in the requested format order."""
    return {key: [f for f in formats if f in set(result.get(key) or [])]
            for key in ("added", "skipped", "failed")}


def _download_formats(fid, formats, title, tmp_dir):
    """Download each format into its own file under `tmp_dir`.

    Returns `(entries, failed)` where entries are `(tmp_path, filename, fmt)`
    tuples. A download failure here is never fatal — the caller reports the
    format as failed and keeps going.
    """
    entries, failed = [], []
    for fmt in formats:
        try:
            content, filename = sidecar.download(fid, fmt, title=title)
        except sidecar.SidecarError as exc:
            log.info("Flibusta: format %s unavailable for id %s (%s)", fmt, fid, exc)
            failed.append(fmt)
            continue
        filename = _safe_filename(filename, fmt)
        # Distinct sub-dir: two formats can legitimately share a filename.
        sub = os.path.join(tmp_dir, fmt)
        try:
            os.makedirs(sub, exist_ok=True)
            path = os.path.join(sub, filename)
            with open(path, "wb") as fh:
                fh.write(content)
        except OSError as exc:
            log.error("Flibusta: could not stage format %s for id %s: %s", fmt, fid, exc)
            failed.append(fmt)
            continue
        entries.append((path, filename, fmt))
    return entries, failed


def _attach_formats(book_id, entries):
    """Attach already-downloaded files to an existing book.

    Mirrors Calibre-Web's "Upload Format" button by calling the very helper that
    backs it (`cps.editbooks.upload_book_formats`, verbatim in README section 3)
    so the file lands in the book's own directory and a `data` row is created.

    `entries` are `(tmp_path, filename, fmt)` tuples. Returns
    `{"added": [...], "skipped": [...], "failed": [...]}`.
    """
    result = {"added": [], "skipped": [], "failed": []}
    if not entries:
        return result

    from werkzeug.datastructures import FileStorage

    from cps.editbooks import upload_book_formats

    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True)
    if book is None:
        log.error("Flibusta: book %s vanished before formats could be attached", book_id)
        result["failed"] = [fmt for _, _, fmt in entries]
        return result

    # Snapshot BEFORE the upload: upload_book_formats logs-and-skips a format the
    # book already carries, and afterwards we cannot tell "was already there"
    # from "we just added it".
    pending, handles, files = [], [], []
    for tmp_path, filename, fmt in entries:
        if calibre_db.get_book_format(book_id, fmt.upper()):
            result["skipped"].append(fmt)
            continue
        try:
            fh = open(tmp_path, "rb")
        except OSError:
            result["failed"].append(fmt)
            continue
        handles.append(fh)
        files.append(FileStorage(
            stream=fh,
            filename=_safe_filename(filename, fmt),
            content_type=_CONTENT_TYPES.get(fmt, "application/octet-stream"),
        ))
        pending.append(fmt)

    if not files:
        return result

    try:
        upload_book_formats(files, book, book_id, getattr(book, "has_cover", False))
        # `error` is a global "at least one file failed" flag and does not say
        # which — so the per-format verdict comes from re-querying instead.
        calibre_db.session.commit()
    except Exception as exc:  # noqa: BLE001 - one bad format must not 500 the add
        log.error("Flibusta: attaching formats to book %s failed: %s", book_id, str(exc))
        with contextlib.suppress(Exception):
            calibre_db.session.rollback()
    finally:
        for fh in handles:
            with contextlib.suppress(Exception):
                fh.close()

    for fmt in pending:
        try:
            present = bool(calibre_db.get_book_format(book_id, fmt.upper()))
        except Exception:  # noqa: BLE001
            present = False
        (result["added"] if present else result["failed"]).append(fmt)
    return result


def _safe_filename(filename, fmt):
    """Never trust the sidecar's Content-Disposition filename.

    It is parsed from a remote header and may contain path separators
    (`../../app/calibre-web/cps/evil.py`), which would escape the temp dir when
    joined. Strip to a bare basename; fall back to `book.<fmt>` when what's left
    is empty or carries no extension (the extension is load-bearing — Calibre-Web
    derives the DB format string from it).
    """
    base = os.path.basename(filename or "").replace("\\", "/")
    base = os.path.basename(base).strip()
    if not base or base in (".", "..") or "." not in base or base.startswith("."):
        return "book.{}".format(fmt)
    return base


def _import_into_library(tmp_path, filename, opds_item, fmt, flibusta_id):
    """Mirror of `cps.editbooks.upload()`'s btn-upload branch (Calibre-Web 0.6.27,
    README section 3), with two adaptations:

    * the file source is a `FileStorage` built from our downloaded temp file
      instead of `request.files`;
    * `metadata.enrich()` runs right after `file_handling_on_upload` so the OPDS
      data (authors/series/tags/description) and the `flibusta:<id>` identifier
      land on `meta` before `create_book_on_upload` persists it.

    Google Drive is not supported by this integration, so only the non-gdrive
    branch of upstream's `if config.config_use_google_drive:` is reproduced.
    Upstream's flash()/WorkerThread/TaskUpload calls are dropped: this is a JSON
    endpoint, not the HTML upload form.
    """
    # Imported lazily so the module can be imported (and tested) without the
    # full Calibre-Web package present.
    from werkzeug.datastructures import FileStorage
    from markupsafe import Markup

    from cps import helper
    from cps.editbooks import (
        create_book_on_upload,
        edit_book_comments,
        file_handling_on_upload,
        move_coverfile,
    )

    db_book = None
    try:
        modify_date = False
        # create the function for sorting... (upstream comment)
        calibre_db.create_functions(config)

        with open(tmp_path, "rb") as fh:
            content_type = _CONTENT_TYPES.get(
                str(fmt).lower().lstrip("."), "application/octet-stream")
            storage = FileStorage(stream=fh, filename=filename, content_type=content_type)
            meta, error = file_handling_on_upload(storage)
            if error:
                raise RuntimeError("file rejected by Calibre-Web")

            meta = metadata.enrich(meta, opds_item, flibusta_id)

            db_book, input_authors, title_dir = create_book_on_upload(modify_date, meta)

            # Comments need book id therefore only possible after flush
            modify_date |= edit_book_comments(Markup(meta.description or "").unescape(), db_book)

            book_id = db_book.id

            # Feed the OPDS cover into meta.cover so move_coverfile copies a real
            # cover instead of Calibre-Web's generic one. BookMeta is immutable.
            cover_path = _fetch_cover_file(flibusta_id, os.path.dirname(tmp_path))
            if cover_path:
                meta = meta._replace(cover=cover_path)

            # non-gdrive branch only (see docstring)
            dir_error = helper.update_dir_structure(
                book_id,
                config.get_book_path(),
                input_authors[0],
                meta.file_path,
                title_dir + meta.extension.lower(),
            )
            if dir_error:
                # Non-fatal upstream too (the DB row is kept), but an on-disk move
                # failure must not vanish silently.
                log.error("Flibusta import: update_dir_structure failed for book %s: %s",
                          book_id, dir_error)
            move_coverfile(meta, db_book)
            if modify_date:
                calibre_db.set_metadata_dirty(book_id)
            # save data to database, reread data
            calibre_db.session.commit()
            helper.add_book_to_thumbnail_cache(book_id)
        return book_id
    except Exception:
        # Spec step 8: the DB rollback alone leaves the on-disk directory that
        # update_dir_structure / move_coverfile may already have created.
        #
        # Order matters in both directions here:
        # * not earlier than this block — create_book_on_upload sets an id-less
        #   `<author>/<title>`, and update_dir_structure then renames the dir to
        #   `<author>/<title> (<id>)` and rewrites db_book.path, so a snapshot
        #   taken inside the try would point at a path that no longer exists;
        # * not after the rollback — rollback() expires/detaches instances, so
        #   reading db_book.path past that point can raise DetachedInstanceError
        #   and mask the original exception. db_book is still live right here.
        book_dir = _library_book_dir(db_book) if db_book is not None else None
        calibre_db.session.rollback()
        if book_dir is not None:
            _safe_rmtree(book_dir)
        raise


def _library_book_dir(db_book):
    """Absolute path of the book's library directory, or None if it can't be
    resolved / would fall outside `config.get_book_path()`."""
    rel = getattr(db_book, "path", None)
    if not rel:
        return None
    try:
        root = os.path.realpath(config.get_book_path())
        candidate = os.path.realpath(os.path.join(root, rel))
    except (TypeError, ValueError, OSError):
        return None
    if candidate == root or not candidate.startswith(root + os.sep):
        return None
    return candidate


def _fetch_cover_file(flibusta_id, dest_dir):
    """Download the OPDS cover into `dest_dir`; return its path or None.

    A missing/broken cover must never fail the import — the caller falls back to
    Calibre-Web's generic cover.
    """
    try:
        path = os.path.join(dest_dir, "cover.jpg")
        # closing(): cover_response is a streamed requests.Response — without an
        # explicit close its connection is never returned to the pool.
        with contextlib.closing(sidecar.cover_response(flibusta_id)) as upstream:
            with open(path, "wb") as fh:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)
        if os.path.getsize(path) == 0:
            os.unlink(path)
            return None
        return path
    except Exception as exc:  # noqa: BLE001 - cover is best-effort
        log.info("Flibusta import: no cover for %s (%s)", flibusta_id, exc)
        return None


def _safe_rmtree(path):
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
