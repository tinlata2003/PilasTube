#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PilasTube platform font bootstrap for ROCKNIX/PortMaster.

The app historically expects SCRIPT_DIR/font.ttf, but the port does not ship
that file and ROCKNIX may store fonts at different paths.  This module keeps
font discovery independent from the working directory and prefers standard
Unicode TTF files before PortMaster theme fonts (some theme fonts are not
compatible with every SDL2_ttf build).
"""

import importlib.abc
import importlib.machinery
import os
import subprocess
import sys


_BASE = os.path.dirname(os.path.abspath(__file__))


def _find_font():
    # PILASTUBE_FONT is an explicit override for debugging/custom ports.
    candidates = [
        os.environ.get("PILASTUBE_FONT"),
        os.path.join(_BASE, "font.ttf"),
        # Standard Unicode fonts. Prefer these over PortMaster theme fonts.
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/usr/local/share/fonts/NotoSans-Regular.ttf",
        "/usr/local/share/fonts/DejaVuSans.ttf",
        os.path.expanduser("~/.local/share/fonts/NotoSans-Regular.ttf"),
        os.path.expanduser("~/.local/share/fonts/DejaVuSans.ttf"),
        os.path.expanduser("~/.fonts/NotoSans-Regular.ttf"),
        os.path.expanduser("~/.fonts/DejaVuSans.ttf"),
        # PortMaster fonts are a last-resort fallback.
        "/opt/system/Tools/PortMaster/themes/default.ttf",
        "/opt/system/Tools/PortMaster/themes/ThemeDefault.ttf",
        "/opt/tools/PortMaster/themes/default.ttf",
        "/roms/ports/PortMaster/themes/default.ttf",
    ]
    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path) and os.access(path, os.R_OK):
            return path

    # fc-match is optional on minimal handheld images.
    for family in ("Noto Sans", "DejaVu Sans", "Liberation Sans"):
        try:
            out = subprocess.check_output(
                ["fc-match", "-f", "%{file}", family],
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

        # Patch only font lookup. The rest of the UI remains untouched.
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
