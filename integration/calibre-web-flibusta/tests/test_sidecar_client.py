import pytest
import responses
from cps_flibusta import sidecar


@responses.activate
def test_search_returns_parsed_json():
    responses.add(responses.GET, "http://sidecar.test/search",
                  json={"results": [{"flibustaId": 1, "title": "X"}], "page": 0, "hasNext": False},
                  status=200)
    out = sidecar.search("x")
    assert out["results"][0]["flibustaId"] == 1


@responses.activate
def test_search_connection_error_raises_unavailable():
    responses.add(responses.GET, "http://sidecar.test/search",
                  body=responses.ConnectionError())
    with pytest.raises(sidecar.SidecarUnavailable):
        sidecar.search("x")


@responses.activate
def test_download_404_raises_book_not_found():
    responses.add(responses.GET, "http://sidecar.test/download/9/fb2",
                  json={"error": "nope"}, status=404)
    with pytest.raises(sidecar.BookNotFound):
        sidecar.download(9, "fb2")


@responses.activate
def test_download_returns_bytes_and_filename():
    responses.add(responses.GET, "http://sidecar.test/download/5/fb2",
                  body=b"<FictionBook/>", status=200,
                  headers={"Content-Disposition": 'attachment; filename="bogatyj_papa.fb2"'})
    content, filename = sidecar.download(5, "fb2", title="Богатый папа")
    assert content == b"<FictionBook/>"
    assert filename == "bogatyj_papa.fb2"
