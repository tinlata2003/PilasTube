# -*- coding: utf-8 -*-
"""Automatic PilasTube updater for ROCKNIX/R36XX.

Run this before importing the main application. It checks the latest main
commit on GitHub, downloads the repository ZIP only when it changed, and
replaces application files while preserving user data and logs.
"""

import json
import os
import shutil
import ssl
import tempfile
import time
import urllib.request
import zipfile

_REPO = "tinlata2003/PilasTube"
_BRANCH = "main"
_BASE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BASE)
_STATE = os.path.join(_BASE, ".update_sha")
_LOG = os.path.join(_BASE, "logs", "detailed.txt")

_API_TIMEOUT = 4
_DOWNLOAD_TIMEOUT = 30

_PRESERVE_FILES = {
    "device.txt",
    "u_preferences.txt",
    "oauth_token.json",
    "yt_favorites.json",
    "yt_history.json",
    "yt_positions.json",
    "yt_blocked.json",
    "yt_searches.json",
    "yt_subs.json",
    "yt_seen.json",
}
_PRESERVE_DIRS = {"logs", ".thumb_cache"}


def _visible(message):
    """Show updater status on the handheld's Linux terminal when possible."""
    try:
        tty = "/dev/tty0"
        if os.path.exists(tty) and os.access(tty, os.W_OK):
            with open(tty, "w", encoding="ascii", errors="replace") as f:
                f.write("\033[2J\033[H")
                f.write("PilasTube\n")
                f.write("==============================\n")
                f.write("AUTO UPDATE\n\n")
                f.write(message[:160] + "\n")
                f.write("\nPlease wait...\n")
                f.flush()
    except Exception:
        pass


def _log(message):
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] [AUTO-UPDATE] %s\n" %
                    (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass
    try:
        print("[AUTO-UPDATE] " + message, flush=True)
    except Exception:
        pass
    _visible(message)


def _ssl_context():
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def _http(url, timeout):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PilasTube-R36XX-Updater/1.0",
            "Accept": "application/vnd.github+json",
        },
    )
    return urllib.request.urlopen(
        req, timeout=timeout, context=_ssl_context()
    )


def _latest_sha():
    url = "https://api.github.com/repos/%s/commits/%s" % (_REPO, _BRANCH)
    with _http(url, _API_TIMEOUT) as r:
        obj = json.loads(r.read().decode("utf-8", "replace"))
    sha = str(obj.get("sha", "")).strip()
    if len(sha) < 7:
        raise RuntimeError("GitHub returned no commit SHA")
    return sha


def _read_state():
    try:
        with open(_STATE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_state(sha):
    tmp = _STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(sha + "\n")
    os.replace(tmp, _STATE)


def _safe_copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def _install_from_zip(archive, sha):
    tmp_root = tempfile.mkdtemp(prefix="pilastube-update-", dir=_ROOT)
    try:
        with zipfile.ZipFile(archive, "r") as z:
            root_real = os.path.realpath(tmp_root)
            for info in z.infolist():
                target = os.path.realpath(os.path.join(tmp_root, info.filename))
                if not (target == root_real or target.startswith(root_real + os.sep)):
                    raise RuntimeError("unsafe update archive path")
            z.extractall(tmp_root)

        roots = [
            os.path.join(tmp_root, n)
            for n in os.listdir(tmp_root)
            if os.path.isdir(os.path.join(tmp_root, n))
        ]
        if len(roots) != 1:
            raise RuntimeError("unexpected GitHub archive layout")
        source_root = roots[0]

        _visible("Installing update %s..." % sha[:12])
        for name in ("PilasTube.sh", "README.md"):
            src = os.path.join(source_root, name)
            if os.path.isfile(src):
                _safe_copy(src, os.path.join(_ROOT, name))

        src_app = os.path.join(source_root, "pilastube")
        if not os.path.isdir(src_app):
            raise RuntimeError("pilastube directory missing in update")

        for current, dirs, files in os.walk(src_app):
            rel = os.path.relpath(current, src_app)
            if rel == ".":
                rel = ""
            dirs[:] = [d for d in dirs if d not in _PRESERVE_DIRS]
            target_dir = os.path.join(_BASE, rel)
            os.makedirs(target_dir, exist_ok=True)
            for name in files:
                if name in _PRESERVE_FILES:
                    continue
                src = os.path.join(current, name)
                dst = os.path.join(target_dir, name)
                _safe_copy(src, dst)

        launcher = os.path.join(_ROOT, "PilasTube.sh")
        try:
            mode = os.stat(launcher).st_mode
            os.chmod(launcher, mode | 0o111)
        except Exception:
            pass

        _write_state(sha)
        return True
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def update_if_needed():
    """Check GitHub and update the installed app if main changed."""
    if os.environ.get("PILASTUBE_AUTO_UPDATE", "1").lower() in {
        "0", "false", "no", "off"
    }:
        _log("disabled by PILASTUBE_AUTO_UPDATE")
        return False

    _visible("Checking GitHub for updates...")
    try:
        latest = _latest_sha()
        current = _read_state()
        if current == latest:
            return False

        _log("UPDATE FOUND: %s -> %s" %
             (current[:10] or "unknown", latest[:10]))
        _visible("UPDATE FOUND!\n%s -> %s" %
                 (current[:12] or "unknown", latest[:12]))
        time.sleep(0.8)

        archive_url = "https://github.com/%s/archive/refs/heads/%s.zip" % (
            _REPO, _BRANCH
        )
        archive = os.path.join(_BASE, ".pilastube-update.zip")
        try:
            _visible("Downloading update %s..." % latest[:12])
            with _http(archive_url, _DOWNLOAD_TIMEOUT) as r, open(archive, "wb") as f:
                shutil.copyfileobj(r, f, length=1024 * 1024)

            if os.path.getsize(archive) < 1024:
                raise RuntimeError("downloaded archive is too small")

            _install_from_zip(archive, latest)
            _log("UPDATE INSTALLED: %s" % latest[:12])
            _visible("UPDATE INSTALLED!\nVersion: %s\nRestarting app..." % latest[:12])
            time.sleep(1.2)
            return True
        finally:
            try:
                os.remove(archive)
            except Exception:
                pass
    except Exception as exc:
        _log("update skipped: %s" % exc)
        _visible("Update skipped. Starting PilasTube...")
        time.sleep(0.5)
        return False


if __name__ == "__main__":
    update_if_needed()
