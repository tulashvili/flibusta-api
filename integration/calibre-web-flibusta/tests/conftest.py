import os
import sys

# Allow importing the package under test and (optionally) calibre-web source
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
CW_SRC = os.path.abspath(os.path.join(HERE, "..", ".calibre-web-src"))
if os.path.isdir(CW_SRC):
    sys.path.insert(0, CW_SRC)

os.environ.setdefault("FLIBUSTA_SIDECAR_URL", "http://sidecar.test")
