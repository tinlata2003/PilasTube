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

# v0.3.9-hotfix3: the external mpv path on the R36XX/ROCKNIX Wayland
# stack was starting successfully and then returning after ~1-2 seconds.
# The previous log only showed "playback finished" because mpv was launched
# with --really-quiet, hiding the actual decoder/audio error. Also, RK3566
# handheld GPU hardware decoding is not required for 640x960 UI playback and
# can make the Wayland/EGL path unstable on some ROCKNIX builds.
#
# Keep the existing player.py unchanged and patch only mpv invocations:
#   * disable hardware decoding for stability;
#   * remove --really-quiet so player-stderr records useful diagnostics;
#   * write a small per-process mpv log under /tmp as a second diagnostic;
#   * leave yt-dlp URLs, audio selection, controls and all other players alone.
try:
    _mpv_patch_installed = False

    # player.py is imported after this bootstrap. Extend the same import hook
    # so the patch is applied as soon as the player module becomes available.
    _previous_import = _builtins.__import__

    def _pilastube_import_mpv(name, globals=None, locals=None, fromlist=(), level=0):
        module = _previous_import(name, globals, locals, fromlist, level)
        if name == "player" and not getattr(module, "_pilastube_mpv_guard", False):
            try:
                _orig_popen = module.subprocess.Popen

                def _pilastube_popen(*args, **kwargs):
                    if args:
                        command = args[0]
                    else:
                        command = kwargs.get("args")
                    try:
                        is_mpv = bool(command and isinstance(command, (list, tuple))
                                      and any(str(x).endswith("/mpv") or str(x) == "mpv"
                                              for x in command[:3]))
                    except Exception:
                        is_mpv = False
                    if is_mpv:
                        cmd = list(command)
                        # Remove quiet mode so the existing player-stderr
                        # thread captures the real reason for an early exit.
                        cmd = [x for x in cmd if str(x) != "--really-quiet"]
                        if "--hwdec=no" not in cmd and "--hwdec=auto" in cmd:
                            cmd = ["--hwdec=no" if str(x) == "--hwdec=auto" else x
                                   for x in cmd]
                        if "--hwdec=no" not in cmd:
                            # Put the option before the URL and after mpv's
                            # normal options; mpv accepts it anywhere before
                            # the input URL.
                            cmd.insert(1, "--hwdec=no")
                        if not any(str(x).startswith("--log-file=") for x in cmd):
                            cmd.insert(1, "--log-file=/tmp/pilastube_mpv_%d.log" % os.getpid())
                        if args:
                            args = (cmd,) + tuple(args[1:])
                        else:
                            kwargs["args"] = cmd
                        print("[PLAYER] mpv stability patch: hwdec=no, verbose stderr enabled")
                    return _orig_popen(*args, **kwargs)

                module.subprocess.Popen = _pilastube_popen
                module._pilastube_mpv_guard = True
                print("[PLAYER] mpv Wayland stability patch enabled")
            except Exception as exc:
                print("[PLAYER] mpv stability patch unavailable: %s" % exc)
        return module

    _builtins.__import__ = _pilastube_import_mpv
except Exception as exc:
    print("[PLAYER] mpv import hook unavailable: %s" % exc)
