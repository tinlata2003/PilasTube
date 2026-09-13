# -*- coding: utf-8 -*-
"""Redirect PilasTube mutable state away from read-only port storage."""
import os
import builtins

BASE = os.path.dirname(os.path.abspath(__file__))
NAMES = {
    "u_preferences.txt", "u_preferences.txt.tmp", "oauth_token.json",
    "yt_favorites.json", "yt_history.json", "yt_positions.json",
    "yt_blocked.json", "yt_searches.json", "yt_subs.json", "yt_seen.json",
}


def _find_dir():
    candidates = [
        os.environ.get("PILASTUBE_USERDATA", "").strip(),
        "/storage/.config/pilastube",
        "/storage/roms/pilastube_userdata",
        "/storage/roms/ports/pilastube_userdata",
        "/var/tmp/pilastube_userdata",
        "/tmp/pilastube_userdata",
    ]
    for path in candidates:
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, ".write-test")
            with builtins.open(test, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test)
            return path
        except Exception:
            continue
    return ""


USERDATA = _find_dir()
if USERDATA:
    _MAP = {os.path.join(BASE, n): os.path.join(USERDATA, n) for n in NAMES}
    _orig_open = builtins.open
    _orig_replace = os.replace
    _orig_exists = os.path.exists
    _orig_isfile = os.path.isfile

    # Copy old state before installing redirects. This preserves the current
    # language/login/history/preferences on the first launch after this fix.
    for _old, _new in list(_MAP.items()):
        try:
            if _orig_isfile(_old) and not _orig_isfile(_new):
                with _orig_open(_old, "rb") as _src:
                    with _orig_open(_new, "wb") as _dst:
                        _dst.write(_src.read())
                print("[STATE] migrated: %s" % os.path.basename(_old))
        except Exception as _exc:
            print("[STATE] migration skipped %s: %s" %
                  (os.path.basename(_old), _exc))

    def _redirect(path):
        try:
            return _MAP.get(os.path.abspath(os.fspath(path)), path)
        except Exception:
            return path

    def _open(file, mode="r", *args, **kwargs):
        return _orig_open(_redirect(file), mode, *args, **kwargs)

    def _replace(src, dst, *args):
        return _orig_replace(_redirect(src), _redirect(dst), *args)

    def _exists(path):
        return _orig_exists(_redirect(path))

    def _isfile(path):
        return _orig_isfile(_redirect(path))

    builtins.open = _open
    os.replace = _replace
    os.path.exists = _exists
    os.path.isfile = _isfile
    print("[STATE] writable user-data: %s" % USERDATA)
    try:
        import yt_extras
        yt_extras.PREFS_FILE = os.path.join(USERDATA, "u_preferences.txt")
        print("[STATE] Preferences redirected to writable storage")
    except Exception as exc:
        print("[STATE] Preferences path patch unavailable: %s" % exc)
else:
    print("[STATE] no writable user-data directory found; using app storage")
