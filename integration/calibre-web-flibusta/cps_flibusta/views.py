"""Flask blueprint that lets Calibre-Web users search Flibusta and import books.

Auth note: Calibre-Web 0.6.27 does NOT ship `flask_login` — it vendors the login
machinery as `cps.cw_login` (see README section 4/8). Import auth helpers from there.
"""

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
    fmt = payload.get("format")
    opds_item = payload.get("item") or {}
    if fid is None or not fmt:
        return jsonify({"error": "flibustaId and format are required"}), 400

    try:
        fid = int(fid)
    except (TypeError, ValueError):
        return jsonify({"error": "flibustaId must be a number"}), 400

    existing = dedup.find_existing(calibre_db, fid)
    if existing:
        return jsonify({"status": "already_exists", "bookId": existing,
                        "url": url_for("web.show_book", book_id=existing)})

    try:
        content, filename = sidecar.download(fid, fmt, title=opds_item.get("title", ""))
    except sidecar.BookNotFound:
        return jsonify({"error": "This book/format is no longer available"}), 404
    except sidecar.SidecarUnavailable:
        return jsonify({"error": "Flibusta service is unavailable"}), 503
    except sidecar.UpstreamError:
        return jsonify({"error": "Flibusta is unreachable, try again later"}), 502

    filename = _safe_filename(filename, fmt)

    tmp_dir = tempfile.mkdtemp(prefix="flibusta_")
    tmp_path = os.path.join(tmp_dir, filename)
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(content)
        book_id = _import_into_library(tmp_path, filename, opds_item, fmt, fid)
    except Exception as exc:  # noqa: BLE001 - session already rolled back below
        return jsonify({"error": "Import failed", "detail": str(exc)}), 500
    finally:
        _safe_rmtree(tmp_dir)

    return jsonify({"status": "added", "bookId": book_id,
                    "url": url_for("web.show_book", book_id=book_id)})


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
        calibre_db.session.rollback()
        raise


def _safe_rmtree(path):
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
