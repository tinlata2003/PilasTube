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

    # v0.3.9-hotfix5/6: external mpv cannot rely on yt-dlp's DASH video-only
    # + audio-only pair when the handheld has no ffmpeg muxer. mpv CAN accept
    # --audio-file, but some YouTube CDN/client combinations return a paired
    # stream that mpv opens inconsistently on this old ROCKNIX build. Prefer a
    # single progressive A/V URL whenever one exists. This also makes playback
    # independent of the language/codec metadata on separate audio tracks.
    _orig_select_formats = _YX.select_formats

    def _pilastube_select_formats(info, quality="Auto", codec="Auto",
                                  ffmpeg_path=None, audio_lang="Original"):
        result = _orig_select_formats(
            info, quality, codec, ffmpeg_path=ffmpeg_path,
            audio_lang=audio_lang)
        try:
            video_url, audio_url, height, note = result
            live = bool(info.get("is_live")) or info.get("live_status") == "is_live"
            # Only force this path when the app is using external mpv. The
            # built-in ffmpeg player is fully capable of separate A/V inputs.
            if video_url and not live and not ffmpeg_path:
                formats = info.get("formats") or []
                cap = _YX.quality_cap(quality)
                candidates = []
                for f in formats:
                    u = f.get("url")
                    vc = str(f.get("vcodec") or "none")
                    ac = str(f.get("acodec") or "none")
                    ext = str(f.get("ext") or "").lower()
                    if not u or vc == "none" or ac == "none":
                        continue
                    if ext not in ("mp4", "webm", "mkv", "mov", "flv", "3gp", ""):
                        continue
                    try:
                        h = int(f.get("height") or 0)
                    except (TypeError, ValueError):
                        h = 0
                    if h <= 0:
                        continue
                    fam = _YX.codec_family(vc)
                    if codec != "Auto" and fam != codec:
                        continue
                    try:
                        tbr = float(f.get("tbr") or 0)
                    except (TypeError, ValueError):
                        tbr = 0.0
                    # H.264 first, then the highest resolution not exceeding
                    # the cap, then bitrate. If every candidate is above the
                    # cap, choose the smallest overshoot. This is deliberately
                    # language-neutral: progressive A/V contains its own audio
                    # and therefore works for Vietnamese, French, Korean, etc.
                    under = 1 if h <= cap else 0
                    distance = -abs(h - cap)
                    h264 = 1 if fam == "H.264" else 0
                    candidates.append((under, h264, distance, tbr, f))
                if candidates:
                    candidates.sort(key=lambda x: x[:4], reverse=True)
                    best = candidates[0][4]
                    fam = _YX.codec_family(best.get("vcodec")) or "A/V"
                    print("[PLAYER] external-mpv progressive: %s %sp" % (
                        fam, best.get("height") or "?"))
                    return (best.get("url"), None,
                            best.get("height") or height or 0,
                            "progressive-av")
        except Exception as exc:
            print("[PLAYER] progressive format patch unavailable: %s" % exc)
        return result

    _YX.select_formats = _pilastube_select_formats
    print("[PLAYER] external-mpv progressive A/V fallback enabled")
except Exception as exc:
    print("[YTDLP] playback format patch unavailable: %s" % exc)

# v0.3.9-hotfix4: when ROCKNIX/mpv uses the Mali hardware decoder through
# Wayland, some VOD codecs/stream profiles can produce audio with a black
# video surface or exit immediately. The same mpv build can still play some
# live H.264 streams, which made the bug look video-specific. Force software
# decoding for the external mpv path; the GPU is still used for rendering.
# mpv documents hwdec=no as the reliable software-decoding mode.
try:
    import builtins as _builtins_hw
    _orig_import_hw = _builtins_hw.__import__

    def _pilastube_import_hw(name, globals=None, locals=None, fromlist=(), level=0):
        module = _orig_import_hw(name, globals, locals, fromlist, level)
        if name == "player":
            try:
                _PM = module.PlayerMixin
                if not getattr(_PM, "_rocknix_hwdec_guard", False):
                    _orig_launch_external = _PM._launch_player_external

                    def _rocknix_launch_external(self, video, video_url, audio_url,
                                                 resume_at, sub_path, chapters_path, info):
                        old_get = getattr(self.prefs, "get", None)
                        if old_get is not None:
                            def _prefs_get(key, default=""):
                                if key == "hwdec":
                                    return "Off"
                                return old_get(key, default)
                            self.prefs.get = _prefs_get
                        try:
                            return _orig_launch_external(
                                self, video, video_url, audio_url, resume_at,
                                sub_path, chapters_path, info)
                        finally:
                            if old_get is not None:
                                self.prefs.get = old_get

                    _PM._launch_player_external = _rocknix_launch_external
                    _PM._rocknix_hwdec_guard = True
                    print("[PLAYER] ROCKNIX external mpv hwdec guard enabled (software decode)")
            except Exception as exc:
                print("[PLAYER] ROCKNIX hwdec guard unavailable: %s" % exc)
        return module

    _builtins_hw.__import__ = _pilastube_import_hw
except Exception as exc:
    print("[PLAYER] hwdec import hook unavailable: %s" % exc)

# v0.3.9-hotfix2: ROCKNIX launches PilasTube under Wayland. In that mode
# destroying SDL's renderer/window immediately before spawning mpv, then
# recreating them while the many image-loader workers are still alive, can
# race inside SDL/EGL and kill the Python process with exit code 139.
# Wayland permits SDL and mpv to own separate surfaces, so keep SDL alive
# while mpv is fullscreen. KMSDRM/X11 retain the old suspend/resume path.
#
# v0.3.9-hotfix3: keeping the SDL window ALIVE but VISIBLE lets the SDL
# surface remain in front of mpv on some Wayland compositors. The result is
# exactly "mpv audio works, video is invisible". Hide the existing SDL window
# instead of destroying it. mpv then gets the topmost visible surface while
# the SDL/EGL objects remain valid, avoiding the old exit-139 race.
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
                        try:
                            if self.window:
                                module.sdl2.SDL_HideWindow(self.window)
                                module.LOG("Wayland: SDL window hidden; mpv owns visible output", "SDL")
                            else:
                                print("[SDL] Wayland: no SDL window to hide")
                        except Exception as exc:
                            print("[SDL] Wayland hide window failed: %s" % exc)

                    def _wayland_resume(self):
                        try:
                            if self.window:
                                module.sdl2.SDL_ShowWindow(self.window)
                                module.sdl2.SDL_RaiseWindow(self.window)
                                module.LOG("Wayland: SDL window restored after mpv", "SDL")
                            else:
                                print("[SDL] Wayland: SDL window missing after mpv")
                        except Exception as exc:
                            print("[SDL] Wayland show window failed: %s" % exc)

                    _PM._sdl_video_suspend = _wayland_suspend
                    _PM._sdl_video_resume = _wayland_resume
                    _PM._wayland_sdl_guard = True
                    print("[SDL] Wayland mpv guard enabled (hide/show, keep EGL alive)")
            except Exception as exc:
                print("[SDL] Wayland mpv guard unavailable: %s" % exc)
        return module

    _builtins.__import__ = _pilastube_import
except Exception as exc:
    print("[SDL] import hook unavailable: %s" % exc)
