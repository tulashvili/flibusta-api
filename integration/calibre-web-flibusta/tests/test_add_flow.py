import os

import pytest
from flask import Flask



@pytest.fixture
def app(monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views, "_require_permission", lambda: None)

    a = Flask(__name__)
    a.register_blueprint(views.flibusta)
    a.config["TESTING"] = True
    # stand-in for Calibre-Web's web.show_book endpoint used by url_for
    a.add_url_rule("/book/<int:book_id>", endpoint="web.show_book", view_func=lambda book_id: "")
    return a


def test_add_returns_already_exists(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: 7)
    # a known book still runs the attach path, so the download must be stubbed
    monkeypatch.setattr(views.sidecar, "download", lambda fid, fmt, title="": (b"d", "b.fb2"))
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "already_exists"
    assert body["bookId"] == 7
    assert body["url"] == "/book/7"


def test_add_download_not_found(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)

    def boom(*a, **k):
        raise views.sidecar.BookNotFound("gone")

    monkeypatch.setattr(views.sidecar, "download", boom)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_add_sidecar_unavailable(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)

    def boom(*a, **k):
        raise views.sidecar.SidecarUnavailable("down")

    monkeypatch.setattr(views.sidecar, "download", boom)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 503


def test_add_upstream_error(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)

    def boom(*a, **k):
        raise views.sidecar.UpstreamError("bad gateway")

    monkeypatch.setattr(views.sidecar, "download", boom)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 502


def test_add_requires_fields(app):
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1})
    assert resp.status_code == 400


def test_add_success_imports_and_returns_url(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    monkeypatch.setattr(views.sidecar, "download", lambda fid, fmt, title="": (b"data", "book.fb2"))

    captured = {}

    def fake_import(tmp_path, filename, opds_item, fmt, flibusta_id):
        captured["tmp_path"] = tmp_path
        captured["filename"] = filename
        captured["flibusta_id"] = flibusta_id
        with open(tmp_path, "rb") as fh:
            captured["content"] = fh.read()
        return 12

    monkeypatch.setattr(views, "_import_into_library", fake_import)
    resp = app.test_client().post(
        "/flibusta/add",
        json={"flibustaId": "1", "format": "fb2", "item": {"title": "T"}},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "added"
    assert body["bookId"] == 12
    assert body["url"] == "/book/12"
    assert body["formats"]["added"] == ["fb2"]
    assert captured["filename"] == "book.fb2"
    assert captured["content"] == b"data"
    assert captured["flibusta_id"] == 1
    # temp dir cleaned up
    assert not os.path.exists(os.path.dirname(captured["tmp_path"]))


def test_add_import_failure_returns_500_and_cleans_up(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    monkeypatch.setattr(views.sidecar, "download", lambda fid, fmt, title="": (b"data", "book.fb2"))

    seen = {}

    def boom(tmp_path, filename, opds_item, fmt, flibusta_id):
        seen["dir"] = os.path.dirname(tmp_path)
        raise RuntimeError("kaboom")

    monkeypatch.setattr(views, "_import_into_library", boom)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 500
    assert "error" in resp.get_json()
    assert not os.path.exists(seen["dir"])


def test_import_into_library_rolls_back_on_error(app, monkeypatch, tmp_path):
    """_import_into_library must rollback the calibre session and re-raise."""
    import sys
    import types

    from cps_flibusta import views

    rolled_back = []
    views.calibre_db.session.rollback = lambda: rolled_back.append(True)
    views.calibre_db.create_functions = lambda cfg: None

    editbooks = sys.modules["cps.editbooks"]
    editbooks.file_handling_on_upload = lambda fs: (_ for _ in ()).throw(RuntimeError("nope"))
    editbooks.create_book_on_upload = lambda modify_date, meta: None
    editbooks.move_coverfile = lambda meta, db_book: None
    editbooks.edit_book_comments = lambda c, b: False

    f = tmp_path / "book.fb2"
    f.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        views._import_into_library(str(f), "book.fb2", {}, "fb2", 1)
    assert rolled_back == [True]


def test_import_into_library_happy_path(app, monkeypatch, tmp_path):
    """Mirror of README section 3 upload(): each helper called in order."""
    import sys
    import types

    from cps_flibusta import views
    from cps_flibusta import metadata

    calls = []

    class Meta:
        file_path = "/tmp/x"
        extension = ".FB2"
        description = "d"
        identifiers = []

        def _replace(self, **kw):
            return self

    meta = Meta()
    db_book = types.SimpleNamespace(id=55)

    editbooks = sys.modules["cps.editbooks"]
    editbooks.file_handling_on_upload = lambda fs: (calls.append("file_handling"), (meta, None))[1]
    editbooks.create_book_on_upload = lambda modify_date, m: (
        calls.append("create_book"),
        (db_book, ["Author"], "title_dir"),
    )[1]
    editbooks.edit_book_comments = lambda c, b: (calls.append("comments"), True)[1]
    editbooks.move_coverfile = lambda m, b: calls.append("move_cover")

    helper = sys.modules["cps.helper"]
    helper.update_dir_structure = lambda *a: calls.append("update_dir") or None
    helper.add_book_to_thumbnail_cache = lambda bid: calls.append("thumbnail")

    views.calibre_db.create_functions = lambda cfg: calls.append("create_functions")
    views.calibre_db.set_metadata_dirty = lambda bid: calls.append("set_dirty")
    views.calibre_db.session.commit = lambda: calls.append("commit")
    views.config.config_use_google_drive = False
    views.config.get_book_path = lambda: "/books"

    monkeypatch.setattr(metadata, "enrich", lambda m, item, fid: (calls.append("enrich"), m)[1])

    f = tmp_path / "book.fb2"
    f.write_bytes(b"x")
    book_id = views._import_into_library(str(f), "book.fb2", {"title": "T"}, "fb2", 7)

    assert book_id == 55
    assert calls == [
        "create_functions",
        "file_handling",
        "enrich",
        "create_book",
        "comments",
        "update_dir",
        "move_cover",
        "set_dirty",
        "commit",
        "thumbnail",
    ]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("book.fb2", "book.fb2"),
        ("../../../app/calibre-web/cps/evil.py", "evil.py"),
        ("../../..", "book.fb2"),
        ("/etc/passwd", "book.fb2"),          # no extension -> fallback
        ("dir/sub/real.epub", "real.epub"),
        ("..\\..\\windows\\evil.fb2", "evil.fb2"),
        ("", "book.fb2"),
        (None, "book.fb2"),
        (".hidden", "book.fb2"),
    ],
)
def test_safe_filename_strips_path_traversal(raw, expected):
    from cps_flibusta import views

    assert views._safe_filename(raw, "fb2") == expected


def test_add_never_writes_outside_tmp_dir(app, monkeypatch):
    """A malicious Content-Disposition filename must not escape the temp dir."""
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    monkeypatch.setattr(
        views.sidecar,
        "download",
        lambda fid, fmt, title="": (b"data", "../../../../tmp/pwned.fb2"),
    )

    seen = {}

    def fake_import(tmp_path, filename, opds_item, fmt, flibusta_id):
        seen["tmp_path"] = os.path.realpath(tmp_path)
        seen["dir"] = os.path.realpath(os.path.dirname(tmp_path))
        seen["filename"] = filename
        return 1

    monkeypatch.setattr(views, "_import_into_library", fake_import)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 200
    assert seen["filename"] == "pwned.fb2"
    assert os.path.dirname(seen["tmp_path"]) == seen["dir"]
    assert "flibusta_" in os.path.basename(seen["dir"])


def test_add_accepts_flibusta_id_zero(app, monkeypatch):
    """A literal id of 0 is a valid id, not a missing field."""
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    monkeypatch.setattr(views.sidecar, "download", lambda fid, fmt, title="": (b"d", "b.fb2"))
    monkeypatch.setattr(views, "_import_into_library", lambda *a: 3)
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 0, "format": "fb2"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "added"


def test_add_still_rejects_missing_format(app):
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": ""})
    assert resp.status_code == 400


def test_import_declares_format_content_type(app, monkeypatch, tmp_path):
    """FileStorage must declare a real content type, not application/octet-stream."""
    import sys

    from cps_flibusta import views

    seen = {}
    editbooks = sys.modules["cps.editbooks"]

    def capture(fs):
        seen["content_type"] = fs.content_type
        raise RuntimeError("stop here")

    editbooks.file_handling_on_upload = capture
    views.calibre_db.create_functions = lambda cfg: None
    views.calibre_db.session.rollback = lambda: None

    f = tmp_path / "book.epub"
    f.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        views._import_into_library(str(f), "book.epub", {}, "epub", 1)
    assert seen["content_type"] == "application/epub+zip"

    with pytest.raises(RuntimeError):
        views._import_into_library(str(f), "book.fb2", {}, "fb2", 1)
    assert seen["content_type"] == "application/x-fictionbook+xml"

    with pytest.raises(RuntimeError):
        views._import_into_library(str(f), "book.djvu", {}, "djvu", 1)
    assert seen["content_type"] == "application/octet-stream"


def test_import_logs_dir_structure_failure(app, monkeypatch, tmp_path):
    """update_dir_structure's return value must not be discarded silently."""
    import sys
    import types

    from cps_flibusta import views
    from cps_flibusta import metadata

    class Meta:
        file_path = "/tmp/x"
        extension = ".FB2"
        description = "d"
        identifiers = []

        def _replace(self, **kw):
            return self

    meta = Meta()
    db_book = types.SimpleNamespace(id=55)
    editbooks = sys.modules["cps.editbooks"]
    editbooks.file_handling_on_upload = lambda fs: (meta, None)
    editbooks.create_book_on_upload = lambda md, m: (db_book, ["A"], "td")
    editbooks.edit_book_comments = lambda c, b: False
    editbooks.move_coverfile = lambda m, b: None

    helper = sys.modules["cps.helper"]
    helper.update_dir_structure = lambda *a: "could not move file"
    helper.add_book_to_thumbnail_cache = lambda bid: None

    views.calibre_db.create_functions = lambda cfg: None
    views.calibre_db.session.commit = lambda: None
    views.config.get_book_path = lambda: "/books"
    monkeypatch.setattr(metadata, "enrich", lambda m, i, f: m)

    logged = []
    monkeypatch.setattr(views.log, "error", lambda *a, **k: logged.append(a))

    f = tmp_path / "book.fb2"
    f.write_bytes(b"x")
    assert views._import_into_library(str(f), "book.fb2", {}, "fb2", 7) == 55
    assert logged, "update_dir_structure failure was not logged"
    assert "could not move file" in str(logged[0])


# --- final-fix wave -------------------------------------------------------

from collections import namedtuple

_BookMeta = namedtuple("BookMeta", "file_path, extension, title, author, cover, description, "
                                   "tags, series, series_id, languages, publisher, pubdate, "
                                   "identifiers")


def _meta(**over):
    base = dict(file_path="/tmp/x.fb2", extension=".FB2", title="T", author="A", cover=None,
                description="d", tags="", series="", series_id="", languages="ru",
                publisher="", pubdate="", identifiers=[])
    base.update(over)
    return _BookMeta(**base)


def _wire_editbooks(tmp_path, book_dir_name="A/T (1)", move_cover=None):
    """Point the cps stubs at a real on-disk library rooted in tmp_path."""
    import sys
    import types

    from cps_flibusta import views

    root = tmp_path / "books"
    book_dir = root / book_dir_name
    book_dir.mkdir(parents=True)
    (book_dir / "book.fb2").write_bytes(b"x")

    db_book = types.SimpleNamespace(id=55, path=book_dir_name)
    editbooks = sys.modules["cps.editbooks"]
    editbooks.file_handling_on_upload = lambda fs: (_meta(), None)
    editbooks.create_book_on_upload = lambda md, m: (db_book, ["A"], "T (1)")
    editbooks.edit_book_comments = lambda c, b: False
    editbooks.move_coverfile = move_cover or (lambda m, b: None)

    helper = sys.modules["cps.helper"]
    helper.update_dir_structure = lambda *a: None
    helper.add_book_to_thumbnail_cache = lambda bid: None

    views.calibre_db.create_functions = lambda cfg: None
    views.calibre_db.set_metadata_dirty = lambda bid: None
    views.calibre_db.session.commit = lambda: None
    views.calibre_db.session.rollback = lambda: None
    views.config.get_book_path = lambda: str(root)
    return root, book_dir


class _CoverUpstream:
    """Stand-in for the streamed requests.Response returned by cover_response."""

    headers = {"Content-Type": "image/jpeg"}
    closed = False

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=8192):
        yield b"\xff\xd8\xff"
        yield b"JPEGBYTES"


def test_import_feeds_opds_cover_into_meta_cover(app, monkeypatch, tmp_path):
    """Item 2: move_coverfile must see a real meta.cover, not None."""
    from cps_flibusta import views
    from cps_flibusta import metadata

    seen = {}
    _wire_editbooks(tmp_path, move_cover=lambda m, b: seen.update(
        cover=m.cover, exists=bool(m.cover) and os.path.exists(m.cover),
        size=os.path.getsize(m.cover) if m.cover and os.path.exists(m.cover) else 0))

    upstream = _CoverUpstream()
    monkeypatch.setattr(metadata, "enrich", lambda m, i, f: m)
    monkeypatch.setattr(views.sidecar, "cover_response", lambda fid: upstream)

    f = tmp_path / "book.fb2"
    f.write_bytes(b"x")
    assert views._import_into_library(str(f), "book.fb2", {}, "fb2", 416925) == 55
    assert seen["cover"], "meta.cover was not set from the OPDS cover"
    assert seen["exists"]
    assert seen["size"] == len(b"\xff\xd8\xffJPEGBYTES")
    assert os.path.dirname(seen["cover"]) == str(tmp_path)
    assert upstream.closed, "the streamed cover response was not closed"


def test_import_survives_a_missing_cover(app, monkeypatch, tmp_path):
    """Item 2: a cover 404 must not fail the import; meta.cover stays None."""
    from cps_flibusta import views
    from cps_flibusta import metadata

    seen = {}
    _wire_editbooks(tmp_path, move_cover=lambda m, b: seen.update(cover=m.cover))

    def boom(fid):
        raise views.sidecar.BookNotFound("no cover")

    monkeypatch.setattr(metadata, "enrich", lambda m, i, f: m)
    monkeypatch.setattr(views.sidecar, "cover_response", boom)

    f = tmp_path / "book.fb2"
    f.write_bytes(b"x")
    assert views._import_into_library(str(f), "book.fb2", {}, "fb2", 1) == 55
    assert seen["cover"] is None


def test_import_removes_library_dir_when_move_coverfile_fails(app, monkeypatch, tmp_path):
    """Item 4 / spec step 8: rollback must clean the on-disk dir too."""
    from cps_flibusta import views
    from cps_flibusta import metadata

    def explode(m, b):
        raise RuntimeError("cover move failed")

    root, book_dir = _wire_editbooks(tmp_path, move_cover=explode)

    # update_dir_structure RENAMES <author>/<title> to <author>/<title> (<id>)
    # and updates db_book.path. The rollback must target the *renamed* dir, so
    # the path cannot be snapshotted before this runs.
    import sys

    db_book = sys.modules["cps.editbooks"].create_book_on_upload(False, None)[0]
    renamed = root / "A" / "T (55)"

    def rename(*a):
        book_dir.rename(renamed)
        db_book.path = "A/T (55)"

    sys.modules["cps.helper"].update_dir_structure = rename

    rolled_back = []
    views.calibre_db.session.rollback = lambda: rolled_back.append(True)
    monkeypatch.setattr(metadata, "enrich", lambda m, i, f: m)
    monkeypatch.setattr(views.sidecar, "cover_response", lambda fid: _CoverUpstream())

    assert book_dir.exists()
    f = tmp_path / "book.fb2"
    f.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        views._import_into_library(str(f), "book.fb2", {}, "fb2", 1)

    assert rolled_back == [True]
    assert not renamed.exists(), "renamed library dir survived the rollback"
    assert not book_dir.exists(), "library dir survived the rollback"
    assert root.exists(), "the library root itself must never be removed"


def test_library_book_dir_refuses_paths_outside_the_library(app, tmp_path):
    """Item 4 guard: a hostile db_book.path must not delete anything outside /books."""
    import types

    from cps_flibusta import views

    root = tmp_path / "books"
    root.mkdir()
    views.config.get_book_path = lambda: str(root)

    assert views._library_book_dir(types.SimpleNamespace(path="../../etc")) is None
    assert views._library_book_dir(types.SimpleNamespace(path="")) is None
    assert views._library_book_dir(types.SimpleNamespace(path=".")) is None
    assert views._library_book_dir(types.SimpleNamespace()) is None


@pytest.mark.parametrize("fmt", ["exe", "../../etc/passwd", "djvu", "sh"])
def test_add_rejects_formats_outside_the_whitelist(app, monkeypatch, fmt):
    """Item 6: the format reaches a URL path and a filename extension."""
    from cps_flibusta import views

    called = []
    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: called.append("dedup"))
    monkeypatch.setattr(views.sidecar, "download",
                        lambda *a, **k: called.append("download") or (b"", "b"))
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": fmt})
    assert resp.status_code == 400
    assert called == []


def test_add_import_failure_hides_exception_detail(app, monkeypatch):
    """Item 11: the client gets a generic message; the detail only goes to the log."""
    from cps_flibusta import views

    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    monkeypatch.setattr(views.sidecar, "download", lambda fid, fmt, title="": (b"d", "b.fb2"))

    def boom(*a, **k):
        raise RuntimeError("/books/secret/path blew up")

    monkeypatch.setattr(views, "_import_into_library", boom)

    logged = []
    monkeypatch.setattr(views.log, "error", lambda *a, **k: logged.append(a))

    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Import failed"}
    assert "detail" not in resp.get_json()
    assert any("secret" in str(entry) for entry in logged)


# --- multi-format add -----------------------------------------------------


@pytest.fixture
def multi(monkeypatch):
    """Reset the multi-format stub state and give tests a wiring handle."""
    import sys
    import types

    from cps_flibusta import views
    from tests import _cps_stubs  # noqa: F401  (already installed by conftest)

    views.calibre_db._formats = set()
    editbooks = sys.modules["cps.editbooks"]

    def default_upload(requested_files, book, book_id, no_cover=True):
        for fs in requested_files:
            views.calibre_db._formats.add(fs.filename.rsplit(".", 1)[-1].upper())
        return {}, False

    editbooks.upload_book_formats = default_upload

    downloads = []
    monkeypatch.setattr(
        views.sidecar, "download",
        lambda fid, fmt, title="": (downloads.append(fmt), (b"data-" + fmt.encode(),
                                                            "book." + fmt))[1])
    imported = []

    def fake_import(tmp_path, filename, opds_item, fmt, flibusta_id):
        imported.append(fmt)
        views.calibre_db._formats.add(fmt.upper())
        return 12

    monkeypatch.setattr(views, "_import_into_library", fake_import)
    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    return types.SimpleNamespace(downloads=downloads, imported=imported,
                                 editbooks=editbooks, views=views)


def test_add_multiple_formats_happy_path(app, multi):
    resp = app.test_client().post(
        "/flibusta/add", json={"flibustaId": 1, "formats": ["fb2", "mobi"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "added"
    assert body["bookId"] == 12
    assert multi.imported == ["fb2"], "primary must import exactly once"
    assert body["formats"]["added"] == ["fb2", "mobi"]
    assert body["formats"]["skipped"] == []
    assert body["formats"]["failed"] == []


def test_add_multiple_formats_secondary_attach_failure(app, multi):
    multi.editbooks.upload_book_formats = lambda files, book, bid, no_cover=True: ({}, True)
    resp = app.test_client().post(
        "/flibusta/add", json={"flibustaId": 1, "formats": ["fb2", "mobi"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["bookId"] == 12, "the primary book must survive a secondary failure"
    assert body["formats"]["added"] == ["fb2"]
    assert body["formats"]["failed"] == ["mobi"]


def test_add_multiple_formats_skips_existing_format(app, multi):
    multi.views.calibre_db._formats.add("MOBI")
    called = []
    multi.editbooks.upload_book_formats = lambda files, book, bid, no_cover=True: (
        called.append([f.filename for f in files]), ({}, False))[1]
    resp = app.test_client().post(
        "/flibusta/add", json={"flibustaId": 1, "formats": ["fb2", "mobi"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["formats"]["skipped"] == ["mobi"]
    assert body["formats"]["added"] == ["fb2"]
    assert body["formats"]["failed"] == []
    assert called == [] or called == [[]], "an already-present format must not be re-uploaded"


def test_add_existing_book_attaches_all_formats(app, multi, monkeypatch):
    monkeypatch.setattr(multi.views.dedup, "find_existing", lambda db, fid: 7)
    resp = app.test_client().post(
        "/flibusta/add", json={"flibustaId": 1, "formats": ["fb2", "epub"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "already_exists"
    assert body["bookId"] == 7
    assert multi.imported == [], "_import_into_library must not run for a known book"
    assert body["formats"]["added"] == ["fb2", "epub"]


def test_add_secondary_download_failure_is_reported(app, multi, monkeypatch):
    from cps_flibusta import views

    def flaky(fid, fmt, title=""):
        if fmt == "mobi":
            raise views.sidecar.BookNotFound("gone")
        return (b"data", "book." + fmt)

    monkeypatch.setattr(views.sidecar, "download", flaky)
    resp = app.test_client().post(
        "/flibusta/add", json={"flibustaId": 1, "formats": ["fb2", "mobi"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "added"
    assert body["formats"]["added"] == ["fb2"]
    assert body["formats"]["failed"] == ["mobi"]


def test_add_singular_format_still_supported(app, multi):
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "format": "fb2"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "added"
    assert body["formats"]["added"] == ["fb2"]


def test_add_rejects_a_bad_format_inside_the_list(app, multi):
    resp = app.test_client().post(
        "/flibusta/add", json={"flibustaId": 1, "formats": ["fb2", "exe"]})
    assert resp.status_code == 400
    assert "exe" in resp.get_json()["error"]
    assert multi.downloads == [], "nothing may be downloaded when validation fails"


def test_add_rejects_an_empty_formats_list(app, multi):
    resp = app.test_client().post("/flibusta/add", json={"flibustaId": 1, "formats": []})
    assert resp.status_code == 400
    assert multi.downloads == []
