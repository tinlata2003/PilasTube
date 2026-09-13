# -*- coding: utf-8 -*-
"""Upload PilasTube diagnostic logs to the project's GitHub repository.

Authentication is intentionally local-only: set PILASTUBE_GITHUB_TOKEN or put
an appropriate fine-grained GitHub token in pilastube/github_token.txt on the
handheld. The token file is preserved by the auto-updater and is never uploaded.
"""

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.request

_REPO = "tinlata2003/PilasTube"
_BRANCH = "main"
_BASE = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = os.path.join(_BASE, "logs")
_DETAILED = os.path.join(_LOG_DIR, "detailed.txt")
_TOKEN_FILE = os.path.join(_BASE, "github_token.txt")
_STATE = os.path.join(_BASE, ".log_upload_sha")


def _token():
    token = str(os.environ.get("PILASTUBE_GITHUB_TOKEN", "")).strip()
    if token:
        return token
    try:
        with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _ssl_context():
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def _request(url, method="GET", data=None, token=None):
    headers = {
        "User-Agent": "PilasTube-R36XX-Diagnostics/1.0",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=12, context=_ssl_context())


def _read_state():
    try:
        with open(_STATE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_state(value):
    try:
        tmp = _STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(value + "\n")
        os.replace(tmp, _STATE)
    except Exception:
        pass


def _redact(text):
    """Remove common credential-looking values before a log leaves the device."""
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("authorization:", "bearer ", "access_token=", "refresh_token=")):
            lines.append("[REDACTED SENSITIVE LINE]")
            continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def upload_log(force=False):
    token = _token()
    if not token:
        return False
    if not os.path.isfile(_DETAILED):
        return False

    try:
        raw = open(_DETAILED, "r", encoding="utf-8", errors="replace").read()
        content = _redact(raw)
        # GitHub Contents API accepts base64 content. Limit each uploaded log
        # to the most recent 350 KB so a damaged log can never make updates huge.
        if len(content.encode("utf-8")) > 350 * 1024:
            content = content[-350 * 1024:]
            content = "[TRUNCATED - newest 350 KB]\n" + content
        digest = __import__("hashlib").sha256(content.encode("utf-8")).hexdigest()
        if not force and digest == _read_state():
            return False

        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = "diagnostics/r36xx-%s.txt" % stamp
        payload = {
            "message": "diagnostics: R36XX log %s" % stamp,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": _BRANCH,
        }

        url = "https://api.github.com/repos/%s/contents/%s" % (_REPO, path)
        try:
            with _request(url, "PUT", json.dumps(payload).encode("utf-8"), token) as r:
                r.read()
        except urllib.error.HTTPError as exc:
            # A retry-safe fallback: if the generated filename already exists,
            # GitHub requires its blob SHA. This is unlikely because the name
            # includes seconds, but handle it cleanly.
            if exc.code == 409:
                return False
            raise

        _write_state(digest)
        print("[LOG-UPLOAD] uploaded %s" % path, flush=True)
        return True
    except Exception as exc:
        print("[LOG-UPLOAD] skipped: %s" % exc, flush=True)
        return False


if __name__ == "__main__":
    upload_log(force=True)
