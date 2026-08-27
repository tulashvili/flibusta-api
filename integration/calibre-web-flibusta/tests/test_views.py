import pytest
from flask import Flask



@pytest.fixture
def app(monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views, "_require_permission", lambda: None)

    flask_app = Flask(__name__)
    flask_app.register_blueprint(views.flibusta)
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def guarded_app():
    """Same blueprint but with the real `_require_permission` left in place."""
    from cps_flibusta import views

    flask_app = Flask(__name__)
    flask_app.register_blueprint(views.flibusta)
    flask_app.config["TESTING"] = True
    return flask_app


def _call(client, route):
    if route.startswith("/flibusta/add"):
        return client.post(route, json={"flibustaId": 1, "format": "fb2"})
    return client.get(route)


@pytest.mark.parametrize("route", ["/flibusta/", "/flibusta/search?q=x", "/flibusta/add"])
def test_routes_403_without_upload_role(guarded_app, monkeypatch, route):
    from cps_flibusta import views

    monkeypatch.setattr(views.current_user, "role_upload", lambda: False)
    monkeypatch.setattr(views.config, "config_uploading", True)
    assert _call(guarded_app.test_client(), route).status_code == 403


@pytest.mark.parametrize("route", ["/flibusta/", "/flibusta/search?q=x", "/flibusta/add"])
def test_routes_403_when_uploading_disabled(guarded_app, monkeypatch, route):
    from cps_flibusta import views

    monkeypatch.setattr(views.current_user, "role_upload", lambda: True)
    monkeypatch.setattr(views.config, "config_uploading", False)
    assert _call(guarded_app.test_client(), route).status_code == 403


def test_routes_allowed_when_permitted(guarded_app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.current_user, "role_upload", lambda: True)
    monkeypatch.setattr(views.config, "config_uploading", True)
    assert guarded_app.test_client().get("/flibusta/").status_code == 200


def test_search_route_proxies_sidecar(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(
        views.sidecar,
        "search",
        lambda q, page=0: {
            "results": [
                {
                    "flibustaId": 1,
                    "title": q,
                    "coverUrl": "/cover/1",
                    "formats": [],
                    "authors": [],
                }
            ],
            "page": 0,
            "hasNext": False,
        },
    )
    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: None)
    resp = app.test_client().get("/flibusta/search?q=hello")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["results"][0]["title"] == "hello"
    assert data["results"][0]["coverUrl"] == "/flibusta/cover/1"
    assert data["results"][0]["alreadyInLibrary"] is False
    assert data["results"][0]["bookId"] is None


def test_search_marks_books_already_in_library(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(
        views.sidecar,
        "search",
        lambda q, page=0: {
            "results": [{"flibustaId": 42, "title": "t", "coverUrl": "/cover/42"}],
            "page": 0,
            "hasNext": False,
        },
    )
    monkeypatch.setattr(views.dedup, "find_existing", lambda db, fid: 99)
    data = app.test_client().get("/flibusta/search?q=x").get_json()
    assert data["results"][0]["alreadyInLibrary"] is True
    assert data["results"][0]["bookId"] == 99


def test_search_without_query_returns_empty(app):
    resp = app.test_client().get("/flibusta/search?q=%20")
    assert resp.status_code == 200
    assert resp.get_json() == {"results": [], "page": 0, "hasNext": False}


def test_search_passes_page_through_as_int(app, monkeypatch):
    from cps_flibusta import views

    seen = {}

    def fake_search(q, page=0):
        seen["page"] = page
        return {"results": [], "page": page, "hasNext": False}

    monkeypatch.setattr(views.sidecar, "search", fake_search)
    app.test_client().get("/flibusta/search?q=x&page=3")
    assert seen["page"] == 3


def test_search_route_reports_sidecar_down(app, monkeypatch):
    from cps_flibusta import views

    def boom(*a, **k):
        raise views.sidecar.SidecarUnavailable("down")

    monkeypatch.setattr(views.sidecar, "search", boom)
    resp = app.test_client().get("/flibusta/search?q=x")
    assert resp.status_code == 503
    assert "error" in resp.get_json()


def test_search_route_reports_upstream_error(app, monkeypatch):
    from cps_flibusta import views

    def boom(*a, **k):
        raise views.sidecar.UpstreamError("bad gateway")

    monkeypatch.setattr(views.sidecar, "search", boom)
    resp = app.test_client().get("/flibusta/search?q=x")
    assert resp.status_code == 502
    assert "error" in resp.get_json()


class _FakeUpstream:
    headers = {"Content-Type": "image/png"}

    def iter_content(self, chunk_size=8192):
        yield b"\x89PNG"


def test_cover_streams_upstream(app, monkeypatch):
    from cps_flibusta import views

    monkeypatch.setattr(views.sidecar, "cover_response", lambda fid: _FakeUpstream())
    resp = app.test_client().get("/flibusta/cover/5")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "image/png"
    assert resp.get_data() == b"\x89PNG"


def test_cover_404_when_sidecar_errors(app, monkeypatch):
    from cps_flibusta import views

    def boom(fid):
        raise views.sidecar.BookNotFound("nope")

    monkeypatch.setattr(views.sidecar, "cover_response", boom)
    assert app.test_client().get("/flibusta/cover/5").status_code == 404


def test_blueprint_contract(app):
    from cps_flibusta import views

    assert views.flibusta.name == "flibusta"
    assert views.flibusta.url_prefix == "/flibusta"
    rules = {r.endpoint: sorted(r.methods & {"GET", "POST"}) for r in app.url_map.iter_rules()}
    assert rules["flibusta.index"] == ["GET"]
    assert rules["flibusta.search"] == ["GET"]
    assert rules["flibusta.cover"] == ["GET"]
    assert rules["flibusta.add"] == ["POST"]


def test_package_exports_blueprint():
    import cps_flibusta

    assert cps_flibusta.flibusta.name == "flibusta"


def test_blueprint_ships_template_and_static_assets():
    import os

    from cps_flibusta import views

    root = os.path.dirname(views.__file__)
    assert os.path.isfile(os.path.join(root, "templates", "flibusta.html"))
    assert os.path.isfile(os.path.join(root, "static", "flibusta.js"))
    assert os.path.isfile(os.path.join(root, "static", "flibusta.css"))
    assert views.flibusta.has_static_folder


def test_index_uses_calibre_web_render_title_template(app):
    """layout.html needs render_title_template's context, not flask.render_template."""
    from cps_flibusta import views

    assert views.render_title_template is not None
    resp = app.test_client().get("/flibusta/")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "rendered:flibusta.html"


def test_index_does_not_use_flask_render_template():
    import inspect

    from cps_flibusta import views

    src = inspect.getsource(views.index)
    assert "render_title_template(" in src
    assert not hasattr(views, "render_template")


def test_js_escapes_quotes_for_attribute_contexts():
    """flibusta.js injects sidecar values into double-quoted HTML attributes."""
    import os

    from cps_flibusta import views

    js = open(os.path.join(os.path.dirname(views.__file__), "static", "flibusta.js")).read()
    for needle in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert needle in js, "esc() must produce {}".format(needle)
