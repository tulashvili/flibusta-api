"""Stubs for the `cps.*` modules that `cps_flibusta.views` imports at import time.

The test-suite must run WITHOUT Calibre-Web installed, so every module the
blueprint touches is replaced by a lightweight stand-in. Installed from
`conftest.py` before any test module is imported.

Auth is stubbed as `cps.cw_login` (NOT `flask_login`): Calibre-Web 0.6.27 vendors
its login machinery there and `flask_login` is not present in the pinned image.
"""

import sys
import types


def _login_required(fn):
    return fn


def install():
    """Install the stub modules into sys.modules (idempotent)."""
    if getattr(sys.modules.get("cps"), "_flibusta_stub", False):
        return sys.modules["cps"]

    cps = types.ModuleType("cps")
    cps._flibusta_stub = True
    cps.__path__ = []  # make it a package so `cps.x` submodules resolve
    cps.calibre_db = types.SimpleNamespace(session=types.SimpleNamespace())
    cps.config = types.SimpleNamespace(config_uploading=True)
    cps.helper = types.ModuleType("cps.helper")
    # used by dedup.find_existing()
    cps.db = types.SimpleNamespace(
        Books=object, Identifiers=types.SimpleNamespace(type=None, val=None)
    )
    sys.modules["cps"] = cps
    sys.modules["cps.editbooks"] = types.ModuleType("cps.editbooks")
    sys.modules["cps.helper"] = cps.helper

    logger_mod = types.ModuleType("cps.logger")
    logger_mod.create = lambda: types.SimpleNamespace(
        error=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
    )
    sys.modules["cps.logger"] = logger_mod
    cps.logger = logger_mod

    # Calibre-Web renders through render_title_template, which injects the extra
    # context layout.html needs (accept, sidebar, g.*).
    render_template_mod = types.ModuleType("cps.render_template")
    render_template_mod.render_title_template = (
        lambda template, **kwargs: "rendered:{}".format(template)
    )
    sys.modules["cps.render_template"] = render_template_mod
    cps.render_template = render_template_mod

    cw_login = types.ModuleType("cps.cw_login")
    cw_login.login_required = _login_required
    cw_login.current_user = types.SimpleNamespace(role_upload=lambda: True)
    sys.modules["cps.cw_login"] = cw_login
    cps.cw_login = cw_login

    usermanagement = types.ModuleType("cps.usermanagement")
    usermanagement.login_required_if_no_ano = _login_required
    sys.modules["cps.usermanagement"] = usermanagement
    cps.usermanagement = usermanagement
    return cps
