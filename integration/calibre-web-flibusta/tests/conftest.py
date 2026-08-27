import os
import sys

# Allow importing the package under test and (optionally) calibre-web source
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
CW_SRC = os.path.abspath(os.path.join(HERE, "..", ".calibre-web-src"))
if os.path.isdir(CW_SRC):
    sys.path.insert(0, CW_SRC)

os.environ.setdefault("FLIBUSTA_SIDECAR_URL", "http://sidecar.test")

# `cps_flibusta.views` imports Calibre-Web modules at import time. Provide stubs so
# the whole suite runs without Calibre-Web installed (real `cps` wins if present).
sys.path.insert(0, HERE)
try:
    from cps import calibre_db as _real_calibre_db  # noqa: F401
except Exception:
    import _cps_stubs

    _cps_stubs.install()
