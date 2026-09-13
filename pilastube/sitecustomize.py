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
# Force the playback-specific client chain explicitly. Feed/search calls are
# left unchanged. This also avoids depending on yt-dlp finding yt-dlp.conf
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

# v0.3.9-hotfix2: ROCKNIX launches PilasTube under Wayland. In that mode
# destroying SDL's renderer/window immediately before spawning mpv, then
# recreating them while the many image-loader workers are still alive, can
# race inside SDL/EGL and kill the Python process with exit code 139.
# Wayland permits SDL and mpv to own separate surfaces, so keep SDL alive
# while mpv is fullscreen. KMSDRM/X11 retain the old suspend/resume path.
try:
    import builtins as _builtins
    _orig_import = _builtins.__import__

    def _pilastube_import(name, globals=None, locals=None, fromlist=(), level=0):
        module = _orig_import(name, globals, locals, fromlist, level)
        if name == "player" and str(os.environ.get("SDL_VIDEODRIVER", "")).lower() == "wayland":
            try:
                _PM = module.PlayerMixin
                if not getattr(_PM, "_wayland_sdl_guard", False):
                    def _wayland_suspend(self):
                        print("[SDL] Wayland: keep SDL surface alive while mpv runs")

                    def _wayland_resume(self):
                        print("[SDL] Wayland: SDL surface already alive after mpv")

                    _PM._sdl_video_suspend = _wayland_suspend
                    _PM._sdl_video_resume = _wayland_resume
                    _PM._wayland_sdl_guard = True
                    print("[SDL] Wayland mpv guard enabled")
            except Exception as exc:
                print("[SDL] Wayland mpv guard unavailable: %s" % exc)
        return module

    _builtins.__import__ = _pilastube_import
except Exception as exc:
    print("[SDL] import hook unavailable: %s" % exc)
