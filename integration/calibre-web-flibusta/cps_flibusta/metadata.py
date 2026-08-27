def _is_empty(value):
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def enrich(meta, opds_item, flibusta_id):
    updates = {}

    if _is_empty(meta.author):
        names = [a.get("name") for a in opds_item.get("authors", []) if a.get("name")]
        if names:
            updates["author"] = " & ".join(names)

    if _is_empty(meta.series) and opds_item.get("series"):
        updates["series"] = opds_item["series"]

    if _is_empty(meta.tags) and opds_item.get("categories"):
        updates["tags"] = ", ".join(opds_item["categories"])

    if _is_empty(meta.description) and opds_item.get("description"):
        updates["description"] = opds_item["description"]

    # Flibusta is a Russian-language library; OPDS carries no language field, so
    # default rather than leave it blank. BookMeta.languages is a string
    # (README section 5) — edit_book_languages() splits it on commas.
    if _is_empty(getattr(meta, "languages", None)):
        updates["languages"] = "ru"

    identifiers = list(meta.identifiers or [])
    if not any(k == "flibusta" for k, _ in identifiers):
        identifiers.append(("flibusta", str(flibusta_id)))
    updates["identifiers"] = identifiers

    return meta._replace(**updates)
