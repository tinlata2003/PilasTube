# -*- coding: utf-8 -*-
"""PilasTube font bootstrap for ROCKNIX/PortMaster.

ROCKNIX images may not ship any usable TTF font. The old implementation only
searched system locations, so PilasTube could start normally while every text
primitive silently returned because no font was loaded. Provision a known
Unicode TTF into the application directory before ui.py is imported.
"""

import os
import ssl
import urllib.request

_BASE = os.path.dirname(os.path.abspath(__file__))
_FONT = os.path.join(_BASE, "font.ttf")
_FONT_URLS = (
    "https://github.com/notofonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf",
    "https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf",
)


def _valid_font(path):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 16384:
            return False
        with open(path, "rb") as f:
            return f.read(4) in (b"\x00\x01\x00\x00", b"true", b"OTTO")
    except Exception:
        return False


def _download_font():
    for url in _FONT_URLS:
        tmp = _FONT + ".download"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PilasTube/0.3.9"})
            with urllib.request.urlopen(
                req, timeout=20, context=ssl._create_unverified_context()
            ) as r:
                data = r.read()
            if len(data) < 16384 or data[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
                continue
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, _FONT)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return False


def _find_existing_font():
    candidates = [
        os.environ.get("PILASTUBE_FONT"),
        _FONT,
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/opt/system/Tools/PortMaster/themes/default.ttf",
        "/opt/system/Tools/PortMaster/themes/ThemeDefault.ttf",
    ]
    for path in candidates:
        if path and _valid_font(path):
            return path
    return None


# Create the local font before PilasTube imports ui.py. ui.py already checks
# SCRIPT_DIR/font.ttf first, so this fixes the original lookup without an
# import hook.
_FONT_PATH = _find_existing_font()
if not _FONT_PATH and _download_font():
    _FONT_PATH = _FONT

if _FONT_PATH:
    os.environ["PILASTUBE_FONT_PATH"] = _FONT_PATH
    try:
        print("[FONT] usable TTF: %s (%d bytes)" %
              (_FONT_PATH, os.path.getsize(_FONT_PATH)))
    except Exception:
        pass
else:
    print("[FONT] ERROR: no usable TTF found and download failed")
