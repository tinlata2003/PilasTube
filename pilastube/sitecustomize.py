# -*- coding: utf-8 -*-
"""PilasTube startup bootstrap.

Checks GitHub for a newer PilasTube build before the application imports its
modules, then loads the cached font/Vietnamese localization bootstrap.
"""

import runpy
import os

_BASE = os.path.dirname(os.path.abspath(__file__))

try:
    from pilastube_updater import update_if_needed
    update_if_needed()
except Exception as exc:
    print("[AUTO-UPDATE] bootstrap failed: %s" % exc)

# The updater may have replaced sitecustomize.py while this module was running.
# Keep the existing font/Vietnamese behavior in a small cached compatibility
# module so the update mechanism does not remove localization.
_locale = os.path.join(_BASE, "sitecustomize_locale.py")
if os.path.isfile(_locale):
    try:
        runpy.run_path(_locale, run_name="_pilastube_locale_loader")
    except Exception as exc:
        print("[LANG] locale loader failed: %s" % exc)
