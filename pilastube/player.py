#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PilasTube v3.5 - player.py (playback domain)

The built-in SmartTube-style player: FFPlayer (one SDL process, ffmpeg
pipes video frames into a streaming texture and PCM audio into SDL_QueueAudio, live HLS master-manifest audio pairing, muxed fallback),
binary discovery (yt-dlp / ffmpeg / external player), playback control
(seek / speed / quality / codec / chapters), the player HUD + options row,
the player menus and the playback queue (PlayerMixin).
"""

import sys
import os
import time
import atexit
import traceback
import threading
import math
import json
import subprocess
import hashlib
import socket
import queue
import ctypes
import urllib.request
import ssl
from datetime import datetime

from utils import (
    ICON_TEX_SIZE, LANG_NAMES, LOG, QUALITY_OPTIONS, SCREEN_HEIGHT,
    SCREEN_WIDTH, UI_SCALE, SCRIPT_DIR, SDL_BLENDMODE_BLEND,
    SDL_BLENDMODE_NONE, SDL_ClearQueuedAudio, SDL_CreateRenderer,
    SDL_CreateTexture, SDL_CreateWindow, SDL_DestroyRenderer,
    SDL_DestroyTexture, SDL_DestroyWindow, SDL_GetTicks,
    SDL_PauseAudioDevice, SDL_QueueAudio, SDL_RENDERER_ACCELERATED,
    SDL_RENDERER_PRESENTVSYNC, SDL_RENDERER_SOFTWARE, SDL_RaiseWindow,
    SDL_Rect, SDL_RenderCopy, SDL_RenderPresent, SDL_SetRenderDrawBlendMode,
    SDL_SetTextureBlendMode, SDL_ShowWindow, SDL_UpdateTexture,
    SDL_WINDOWPOS_CENTERED, SLOG, SPEED_STEPS, _PLOG, _get_icon_texture,
    _sdl_err, fmt_clock, run_logged_thread, sdl2,
)
import yt_extras as YX

# SponsorBlock category colors on the player progress bar (SmartTube-style)
SB_COLORS = {
    "sponsor": (255, 204, 0), "selfpromo": (255, 120, 0),
    "interaction": (170, 120, 255), "intro": (100, 200, 255),
    "outro": (100, 200, 255), "preview": (120, 255, 170),
    "filler": (200, 200, 200), "music_offtopic": (255, 120, 200),
}


def find_ytdlp():
    paths = [os.path.join(SCRIPT_DIR, "yt-dlp"), os.path.expanduser("~/.local/bin/yt-dlp"),
             "/home/ark/.local/bin/yt-dlp", "/usr/local/bin/yt-dlp", "/usr/bin/yt-dlp"]
    for p in paths:
        if os.path.exists(p):
            try: os.chmod(p, 0o755)
            except Exception: pass
            return p
    try:
        result = subprocess.run(["which", "yt-dlp"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip(): return result.stdout.strip()
    except Exception: pass
    return None


def find_video_player():
    players = ["mpv", "ffplay", "vlc", "mplayer"]
    for player in players:
        try:
            result = subprocess.run(["which", player], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip(): return result.stdout.strip()
        except Exception: continue
    pm_ffplay = "/opt/system/Tools/PortMaster/libs/ffplay"
    if os.path.exists(pm_ffplay): return pm_ffplay
    return None


def find_ffmpeg():
    candidates = ["ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg",
                  "/opt/system/Tools/PortMaster/libs/ffmpeg"]
    for cand in candidates:
        try:
            if os.sep not in cand:
                result = subprocess.run(["which", cand], stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip(): return result.stdout.strip()
            elif os.path.isfile(cand) and os.access(cand, os.X_OK): return cand
        except Exception: continue
    return None

YTDLP_PATH = find_ytdlp()
VIDEO_PLAYER = find_video_player()
FFMPEG_PATH = find_ffmpeg()
PLAYER_IS_MPV = VIDEO_PLAYER and os.path.basename(VIDEO_PLAYER) == "mpv"
USE_BUILTIN_PLAYER = bool(FFMPEG_PATH)
LOG("ffmpeg for built-in player: %s" % (FFMPEG_PATH or "NOT FOUND"), "PLAYER")

AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_BYTES_PER_SEC = AUDIO_RATE * AUDIO_CHANNELS * 2
AUDIO_LEAD_SECONDS = 1.2
SEEK_COOLDOWN_MS = 700
MAX_VIDEO_QUEUE = 4


def _short_cmd_part(arg, limit=90):
    s = str(arg)
    if s.startswith(("http://", "https://")) and len(s) > limit: return s[:limit] + "..."
    return s

# NOTE: the remainder of this file is intentionally preserved by this hotfix.
