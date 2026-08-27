import os
import re
import requests

SIDECAR_URL = os.environ.get("FLIBUSTA_SIDECAR_URL", "http://flibusta-sidecar:8080").rstrip("/")
TIMEOUT = float(os.environ.get("FLIBUSTA_SIDECAR_TIMEOUT", "30"))


class SidecarError(Exception):
    pass


class SidecarUnavailable(SidecarError):
    pass


class BookNotFound(SidecarError):
    pass


class UpstreamError(SidecarError):
    pass


def _get(path, **kwargs):
    try:
        return requests.get(f"{SIDECAR_URL}{path}", timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise SidecarUnavailable(str(exc)) from exc


def _raise_for_status(resp):
    if resp.status_code == 404:
        raise BookNotFound(_error_message(resp))
    if resp.status_code in (502, 504):
        raise UpstreamError(_error_message(resp))
    if resp.status_code >= 400:
        raise SidecarError(_error_message(resp))


def _error_message(resp):
    try:
        return resp.json().get("error", resp.text)
    except ValueError:
        return resp.text or f"HTTP {resp.status_code}"


def search(q, page=0):
    resp = _get("/search", params={"q": q, "page": page})
    _raise_for_status(resp)
    return resp.json()


def cover_response(flibusta_id):
    resp = _get(f"/cover/{int(flibusta_id)}", stream=True)
    _raise_for_status(resp)
    return resp


def download(flibusta_id, fmt, title=""):
    resp = _get(f"/download/{int(flibusta_id)}/{fmt}", params={"title": title})
    _raise_for_status(resp)
    return resp.content, _filename_from_disposition(resp) or f"book.{fmt}"


def _filename_from_disposition(resp):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename="([^"]+)"', cd)
    return m.group(1) if m else None
