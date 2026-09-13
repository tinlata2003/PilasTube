#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PilasTube platform font bootstrap.

ROCKNIX/PortMaster images do not always keep DejaVu Sans at the exact paths
used by the original UI code. Python automatically imports sitecustomize when
this directory is on PYTHONPATH, so use it to locate an installed TTF before
PilasTube imports its UI module.

This does not replace the app's renderer or SDL setup; it only redirects the
legacy bundled-font request to a real readable system/PortMaster font.
"""

import os
import subprocess


def _find_font():
    candidates = [
        os.environ.get("PILASTUBE_FONT"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "font.ttf"),
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/usr/local/share/fonts/DejaVuSans.ttf",
        "/opt/system/Tools/PortMaster/themes/default.ttf",
        "/opt/system/Tools/PortMaster/themes/ThemeDefault.ttf",
        "/opt/tools/PortMaster/themes/default.ttf",
        "/roms/ports/PortMaster/themes/default.ttf",
        os.path.expanduser("~/.local/share/fonts/DejaVuSans.ttf"),
        os.path.expanduser("~/.fonts/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.R_OK):
            return path

    # Fontconfig is present on many ROCKNIX/PortMaster builds even when the
    # font is outside the hard-coded FHS paths above.
    try:
        out = subprocess.check_output(
            ["fc-match", "-f", "%{file}", "DejaVu Sans"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode("utf-8", "replace").strip()
        if out and os.path.isfile(out) and os.access(out, os.R_OK):
            return out
    except Exception:
        pass
    return None


_FONT = _find_font()
if _FONT:
    os.environ["PILASTUBE_FONT_PATH"] = _FONT

    # ui.py historically checks for SCRIPT_DIR/font.ttf first and returns
    # that path. Keep that source code compatible while redirecting the actual
    # SDL_ttf open call to the discovered system font.
    try:
        import sdl2.sdlttf as _ttf
        _open_font = _ttf.TTF_OpenFont

        def _patched_open_font(path, ptsize):
            try:
                if isinstance(path, bytes):
                    requested = path.decode("utf-8", "replace")
                else:
                    requested = str(path)
                if requested.endswith("/font.ttf") and requested != _FONT:
                    path = _FONT.encode("utf-8")
            except Exception:
                pass
            return _open_font(path, ptsize)

        _ttf.TTF_OpenFont = _patched_open_font
    except Exception:
        pass

    # Make the first legacy path appear available to ui.py so it reaches the
    # patched TTF_OpenFont call even when no font.ttf is bundled in the port.
    try:
        _exists = os.path.exists
        _legacy_font = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "font.ttf")

        def _patched_exists(path):
            try:
                if os.path.abspath(str(path)) == _legacy_font:
                    return True
            except Exception:
                pass
            return _exists(path)

        os.path.exists = _patched_exists
    except Exception:
        pass
