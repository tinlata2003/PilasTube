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

_locale = os.path.join(_BASE, "sitecustomize_locale.py")
if os.path.isfile(_locale):
    try:
        runpy.run_path(_locale, run_name="_pilastube_locale_loader")
    except Exception as exc:
        print("[LANG] locale loader failed: %s" % exc)

# v0.3.9-hotfix: YouTube can return LOGIN_REQUIRED / "Sign in to confirm
# you're not a bot" for the default yt-dlp client on some IP/device paths.
# Force the playback-specific client chain explicitly.  Feed/search calls are
# left unchanged.  This also avoids depending on yt-dlp finding yt-dlp.conf
# beside the standalone binary.
try:
    import yt_extras as _YX
    _orig_ytdlp_common_args = _YX.ytdlp_common_args

    def _pilastube_ytdlp_common_args(prefs, for_video=False):
        args = list(_orig_ytdlp_common_args(prefs, for_video=for_video))
        if for_video:
            cleaned = []
            i = 0
            while i < len(args):
                if args[i] == "--extractor-args" and i + 1 < len(args):
                    value = str(args[i + 1])
                    if value.startswith("youtube:player_client="):
                        i += 2
                        continue
                cleaned.append(args[i])
                i += 1
            args = cleaned
            args += [
                "--extractor-args",
                "youtube:player_client=web_embedded,tv,android_vr",
            ]
        return args

    _YX.ytdlp_common_args = _pilastube_ytdlp_common_args
    print("[YTDLP] playback client fallback enabled: web_embedded,tv,android_vr")
except Exception as exc:
    print("[YTDLP] playback client patch unavailable: %s" % exc)
