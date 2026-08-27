from collections import namedtuple
from cps_flibusta import metadata

# Mirror of cps.uploader.BookMeta - keep field list in sync with Task 6 findings
BookMeta = namedtuple("BookMeta", "file_path, extension, title, author, cover, description, "
                                  "tags, series, series_id, languages, publisher, pubdate, identifiers")


def _blank(**over):
    base = dict(file_path="/tmp/x.fb2", extension=".fb2", title="", author="", cover=None,
                description="", tags="", series="", series_id="", languages="",
                publisher="", pubdate="", identifiers=[])
    base.update(over)
    return BookMeta(**base)


def test_enrich_fills_empty_fields_from_opds():
    meta = _blank(title="Богатый папа")
    opds = {"authors": [{"name": "Кийосаки Роберт"}], "series": "Богатый папа",
            "categories": ["Финансы", "Карьера"], "description": "<p>desc</p>"}
    out = metadata.enrich(meta, opds, 416925)
    assert out.author == "Кийосаки Роберт"
    assert out.series == "Богатый папа"
    assert "Финансы" in out.tags
    assert out.description == "<p>desc</p>"
    assert ("flibusta", "416925") in list(out.identifiers)


def test_enrich_does_not_overwrite_existing_file_metadata():
    meta = _blank(title="T", author="File Author", series="File Series",
                  description="from file")
    opds = {"authors": [{"name": "OPDS Author"}], "series": "OPDS Series",
            "categories": [], "description": "from opds"}
    out = metadata.enrich(meta, opds, 1)
    assert out.author == "File Author"
    assert out.series == "File Series"
    assert out.description == "from file"
