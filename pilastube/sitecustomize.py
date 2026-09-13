#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PilasTube platform font bootstrap for ROCKNIX/PortMaster.

The app historically expects SCRIPT_DIR/font.ttf, but the port does not ship
that file and ROCKNIX may store fonts at different paths. sitecustomize is
loaded automatically because pilastube is on PYTHONPATH. We install a small
import hook for ui.py so the real SDL2 modules are loaded normally first, then
UIMixin._find_font is replaced with a runtime font locator.
"""

import importlib.abc
import importlib.machinery
import os
import subprocess
import sys


_BASE = os.path.dirname(os.path.abspath(__file__))


def _find_font():
    candidates = [
        os.environ.get("PILASTUBE_FONT"),
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


class _UIFontLoader(importlib.abc.Loader):
    def __init__(self, real_spec):
        self.real_spec = real_spec

    def create_module(self, spec):
        if self.real_spec.loader and hasattr(self.real_spec.loader, "create_module"):
            return self.real_spec.loader.create_module(spec)
        return None

    def exec_module(self, module):
        loader = self.real_spec.loader
        if loader is None or not hasattr(loader, "exec_module"):
            raise ImportError("cannot load ui.py")
        loader.exec_module(module)

        # Patch only the font lookup. The rest of the UI/rendering code stays
        # untouched, so this is safe across future PilasTube UI changes.
        if _FONT:
            def _find_font(self):
                return _FONT
            module.UIMixin._find_font = _find_font
            try:
                module.LOG("ROCKNIX font selected: %s" % _FONT, "FONT")
            except Exception:
                pass
        else:
            try:
                module.LOG("WARN: ROCKNIX font discovery found no readable TTF", "FONT")
            except Exception:
                pass


class _UIFontFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "ui":
            return None
        # Bypass this finder while resolving the real ui.py.
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            if self not in sys.meta_path:
                sys.meta_path.insert(0, self)
        if spec is None:
            return None
        spec.loader = _UIFontLoader(spec)
        return spec


if _FONT:
    sys.meta_path.insert(0, _UIFontFinder())
