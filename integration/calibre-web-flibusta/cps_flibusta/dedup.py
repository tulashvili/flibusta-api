def find_existing(calibre_db, flibusta_id):
    """Return the Calibre book id carrying identifier flibusta:<id>, or None."""
    from cps import db  # imported lazily so tests can run without calibre-web

    row = (
        calibre_db.session.query(db.Books)
        .join(db.Identifiers)
        .filter(db.Identifiers.type == "flibusta")
        .filter(db.Identifiers.val == str(flibusta_id))
        .first()
    )
    return row.id if row is not None else None
