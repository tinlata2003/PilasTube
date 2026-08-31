# -*- coding: utf-8 -*-
"""
yt_extras.py - Service layer for PilasTube (PortMaster YouTube client)

Provides:
  - Preferences        : human-readable u_preferences.txt store (first-boot wizard)
  - SubscriptionsManager: local channel subscriptions via YouTube RSS feeds
  - SponsorBlockClient : in-video segment skipping (sponsor/selfpromo/intro/...)
  - DeArrowClient      : crowd-sourced clickbait-free titles
  - RYDClient          : Return YouTubeDislike vote counts
  - fetch_suggestions  : live search suggestions (keyless Google endpoint)
  - format selection   : best AVC/H.264 stream with height cap + DASH audio pairing
  - chapters/subtitles helpers for mpv
  - yt_dlp_self_update : in-app updater for the bundled yt-dlp binary

Everything here is stdlib-only and DEFENSIVELY imported: firmware Python
builds (Knulli / buildroot) sometimes ship without the optional stdlib
pieces (xml.etree, concurrent.futures). This module degrades gracefully:
RSS feeds are parsed with a regex fallback and subscription fetching
falls back to sequential loading when the thread pool is unavailable.
"""

import os
import sys
import json
import time
import re
import ssl
import hashlib
import socket
import subprocess
import threading
import urllib.request
import urllib.parse

# --- optional stdlib pieces (absent on some minimal firmware builds) -------
try:
    import xml.etree.ElementTree as ET
    HAS_ET = True
except ImportError:                                        # pragma: no cover
    ET = None
    HAS_ET = False

try:
    from concurrent.futures import ThreadPoolExecutor
    HAS_CONCURRENT = True
except ImportError:                                        # pragma: no cover
    ThreadPoolExecutor = None
    HAS_CONCURRENT = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PREFS_FILE = os.path.join(SCRIPT_DIR, "u_preferences.txt")

APP_NAME = "PilasTube"
APP_VERSION = "0.3.7"

# Lenient SSL context - handheld firmwares often carry outdated CA bundles.
# (Same behaviour as v1 of the app; only used for read-only public APIs.)
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

# ----------------------------------------------------------------------------
# v3.3: SmartTube-style account login (YouTube TV device-code OAuth)
#
# Same flow the SmartTube Android app uses (their YTSignInPresenter +
# MediaServiceCore AuthApi): POST a device-code request to youtube.com's
# OAuth proxy, show the user_code, the user enters it at yt.be/activate on
# another device, and we poll /o/oauth2/token until a refresh_token appears.
# The resulting Bearer token authenticates youtubei (innertube) calls:
# account identity (accounts_list) + the account's own subscriptions feed
# and watch history (browse). Live-verified against the real endpoints.
# ----------------------------------------------------------------------------
TV_USER_AGENT = ("Mozilla/5.0 (Linux armeabi-v7a; Android 7.1.2; Fire OS 6.0) "
                 "Cobalt/22.lts.3.306369-gold (unlike Gecko) v8/8.8.278.8-jit "
                 "gles Starboard/13, Amazon_ATV_mediatek8695_2019/NS6294 "
                 "(Amazon, AFTMM, Wireless) com.amazon.firetv.youtube/22.3.r2.v66.0")
TV_CLIENT_ID = ("861556708454-d6dlm3lh05idd8npek18k6be8ba3oc68"
                ".apps.googleusercontent.com")
TV_CLIENT_SECRET = "SboVhoG9s0rNafixCSGGKXAT"
TV_SCOPE = ("http://gdata.youtube.com "
            "https://www.googleapis.com/auth/youtube-paid-content")
TV_MODEL_NAME = "ytlr::"
OAUTH_DEVICE_URL = "https://www.youtube.com/o/oauth2/device/code"
OAUTH_TOKEN_URL = "https://www.youtube.com/o/oauth2/token"
INNERTUBE_API = "https://www.youtube.com/youtubei/v1"
INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"   # public web key
# v0.3.7: fallback TV client version (the live one is scraped from /tv by
# get_tv_app_info(); SmartTube ships 7.20260707.07.00, the TV page itself
# advertises 7.20260826.15.00 as of 2026-08-31)
TV_CLIENT_VERSION = "7.20260826.15.00"
# v3.6: anonymous keyless browse uses the WEB client with browser-like
# headers (X-YouTube-Client-*). Version verified working 2026-08-31 - the
# keyless browse channel feed + search both answer with it.
WEB_CLIENT_VERSION = "2.20260708.00.00"
WEB_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36")
SIGNIN_URL = "https://yt.be/activate"     # what SmartTube shows on screen


# ----------------------------------------------------------------------------
# Preferences (u_preferences.txt)
# ----------------------------------------------------------------------------
DEFAULT_PREFS = {
    # first-boot wizard
    "first_boot_done": "0",
    "device": "R36S",
    "wizard_date": "",
    # general
    "language": "English",
    "theme": "Dark",
    "search_count": "16",
    "auto_load": "On",
    # playback
    "quality": "Auto",
    "video_codec": "Auto",          # v3.2: Auto / H.264 / VP9 / AV1
    "hwdec": "On",
    "seek_interval": "10",
    "playback_mode": "Normal",
    "speed_memory": "On",
    "last_speed": "1.0",
    "subtitles": "Auto",
    "audio_lang": "Original",       # v3.6: audio track language
    "sleep_timer": "Off",
    "volume_boost": "Off",
    # smarttube-inspired extras
    "sponsorblock": "Core",
    "dearrow": "Off",
    "ryd": "On",
    "suggestions": "On",
    "hide_shorts": "Off",
    "hide_live": "Off",
    "hide_watched": "Off",
    # network / route options
    "proxy": "",
    "prefer_ipv4": "Off",
    "route": "Default",             # v3.2: Default / Cronet / OkHttp
    "player_client": "Default",
    "socket_timeout": "15",
    "sb_server": "https://sponsor.ajay.app",
    # metadata
    "app_version": APP_VERSION,
}


class Preferences(object):
    """Key=value text store saved as u_preferences.txt (human-editable)."""

    def __init__(self, path=PREFS_FILE):
        self.path = path
        self.data = dict(DEFAULT_PREFS)
        self._lock = threading.Lock()
        self.load()

    def load(self):
        # 1) load defaults
        # 2) overlay u_preferences.txt if present
        # 3) migrate legacy yt_settings.json once if it exists
        merged = dict(DEFAULT_PREFS)
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip()
                            if k and v:
                                merged[k] = v
            except Exception as e:
                print("Preferences load error: %s" % e)
        else:
            legacy = os.path.join(SCRIPT_DIR, "yt_settings.json")
            if os.path.exists(legacy):
                try:
                    with open(legacy, "r", encoding="utf-8") as f:
                        old = json.load(f)
                    for k in ("language", "quality", "search_count", "auto_load"):
                        if k in old:
                            merged[k] = str(old[k])
                except Exception:
                    pass
        self.data = merged

    def get(self, key, default=""):
        with self._lock:
            return self.data.get(key, default)

    def set(self, key, value, save=True):
        with self._lock:
            self.data[key] = str(value)
        if save:
            self.save()

    def save(self):
        with self._lock:
            snapshot = dict(self.data)
        try:
            lines = [
                "# PilasTube - user preferences",
                "# Generated by the first-boot wizard / Settings screen.",
                "# You can edit this file by hand; format is key=value",
                "",
            ]
            order = [
                ("first_boot_done", "First boot wizard completed (1=yes)"),
                ("device", "Device model"),
                ("wizard_date", "Wizard completion date"),
                ("app_version", "App version"),
                ("language", "UI language"),
                ("theme", "Theme: Dark / OLED"),
                ("quality", "Video quality: Auto/240p/360p/480p/720p"),
                ("hwdec", "Hardware decoding: On/Off"),
                ("seek_interval", "Seek interval (L/R buttons) seconds"),
                ("playback_mode", "Playback mode: Normal/Autoplay/Repeat One/Shuffle"),
                ("speed_memory", "Remember playback speed: On/Off"),
                ("last_speed", "Last used playback speed"),
                ("subtitles", "Subtitles: Auto/Off/en/pt/es/tr/de/fr"),
                ("audio_lang", "Audio track language: Original/pt/en/es/..."),
                ("sleep_timer", "Sleep timer: Off/15/30/60 (minutes)"),
                ("volume_boost", "Allow volume over 100%: On/Off"),
                ("sponsorblock", "SponsorBlock: Off/Minimal/Core/All"),
                ("sb_server", "SponsorBlock server URL"),
                ("dearrow", "DeArrow better titles: On/Off"),
                ("ryd", "Show dislike counts (RYD): On/Off"),
                ("suggestions", "Search suggestions: On/Off"),
                ("hide_shorts", "Hide Shorts: On/Off"),
                ("hide_live", "Hide live streams: On/Off"),
                ("hide_watched", "Hide watched videos: On/Off"),
                ("search_count", "Results per search page"),
                ("auto_load", "Auto-load Home feed: On/Off"),
                ("proxy", "HTTP(S)/SOCKS proxy URL (empty=direct)"),
                ("prefer_ipv4", "Prefer IPv4: On/Off"),
                ("player_client", "yt-dlp YouTube client: Default/web/tv/android/ios"),
                ("socket_timeout", "Network socket timeout (seconds)"),
            ]
            seen = set()
            for key, comment in order:
                if key in snapshot:
                    lines.append("# %s" % comment)
                    lines.append("%s=%s" % (key, snapshot[key]))
                    lines.append("")
                    seen.add(key)
            for k in sorted(snapshot.keys()):
                if k not in seen:
                    lines.append("%s=%s" % (k, snapshot[k]))
                    lines.append("")
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            os.replace(tmp, self.path)
        except Exception as e:
            print("Preferences save error: %s" % e)


# ----------------------------------------------------------------------------
# Network helpers (proxy / ipv4 aware, SmartTube-style routes)
# ----------------------------------------------------------------------------
# v3.2 "route" flavors, mirroring SmartTube's backend selector:
#   Default : plain urllib opener per request (previous behaviour)
#   Cronet  : IPv4-preferring + fast retry (Chromium-style resilience for
#             flaky handheld wifi)
#   OkHttp  : persistent keep-alive connection pool with automatic re-open
#             on stale sockets (saves a full TLS handshake per request for
#             thumbnails / suggestions / SponsorBlock)
ROUTE_OPTIONS = ["Default", "Cronet", "OkHttp"]
_ACTIVE_ROUTE = "Default"


def set_active_route(route):
    """Select the HTTP flavor used by every net_get/net_get_json call."""
    global _ACTIVE_ROUTE
    _ACTIVE_ROUTE = route if route in ROUTE_OPTIONS else "Default"


def get_active_route():
    return _ACTIVE_ROUTE


def _proxy_handler(proxy_url):
    if proxy_url:
        return urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        })
    return urllib.request.ProxyHandler({})


# --- OkHttp-style persistent connection pool --------------------------------
_CONN_POOL = {}
_POOL_LOCK = threading.Lock()


def _pooled_request(url, timeout, headers):
    """Keep-alive GET through a cached http.client connection.

    Mirrors OkHttp's ConnectionPool + retryOnConnectionFailure: a stale
    keep-alive socket is retried once on a fresh connection."""
    import http.client
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    key = (scheme, host, port)
    attempt = 0
    while True:
        conn = None
        with _POOL_LOCK:
            conn = _CONN_POOL.pop(key, None)
        fresh = conn is None
        if conn is None:
            if scheme == "https":
                conn = http.client.HTTPSConnection(
                    host, port, timeout=timeout, context=_ssl_ctx)
            else:
                conn = http.client.HTTPConnection(host, port,
                                                  timeout=timeout)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                raise OSError("HTTP %d" % resp.status)
            if resp.will_close:
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                with _POOL_LOCK:
                    if len(_CONN_POOL) < 8:
                        _CONN_POOL[key] = conn
                    else:
                        try:
                            conn.close()
                        except Exception:
                            pass
            return data
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            if fresh or attempt >= 1:
                raise
            attempt += 1      # stale pooled socket: one clean retry


def close_connection_pool():
    """Drop every pooled keep-alive connection (used on route change)."""
    with _POOL_LOCK:
        conns = list(_CONN_POOL.values())
        _CONN_POOL.clear()
    for c in conns:
        try:
            c.close()
        except Exception:
            pass


def net_get(url, timeout=10, proxy="", prefer_ipv4=False, headers=None,
            route=None):
    """GET a URL, return bytes. Raises on failure."""
    hdrs = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36"}
    if headers:
        hdrs.update(headers)
    route = route or _ACTIVE_ROUTE
    # OkHttp route: pooled keep-alive (no proxy support - pooled requests
    # fall back to the Default route when a proxy is configured)
    if route == "OkHttp" and not proxy and url.startswith("http"):
        try:
            return _pooled_request(url, timeout, hdrs)
        except Exception:
            pass    # fall through to the plain urllib path
    if route == "Cronet":
        # Chromium-style: prefer IPv4, connect fast, retry once quickly
        prefer_ipv4 = True
        try:
            return _net_get_urllib(url, min(timeout, 6), proxy, True, hdrs)
        except Exception:
            return _net_get_urllib(url, timeout, proxy, prefer_ipv4, hdrs)
    return _net_get_urllib(url, timeout, proxy, prefer_ipv4, hdrs)


def _net_get_urllib(url, timeout, proxy, prefer_ipv4, hdrs):
    req = urllib.request.Request(url, headers=hdrs)
    opener = urllib.request.build_opener(
        _proxy_handler(proxy), urllib.request.HTTPSHandler(context=_ssl_ctx))
    if prefer_ipv4:
        old = socket.getaddrinfo

        def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
            return old(host, port, socket.AF_INET, type, proto, flags)
        socket.getaddrinfo = ipv4_only
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        finally:
            socket.getaddrinfo = old
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def net_get_json(url, timeout=10, proxy="", prefer_ipv4=False):
    raw = net_get(url, timeout=timeout, proxy=proxy, prefer_ipv4=prefer_ipv4)
    return json.loads(raw.decode("utf-8", "replace"))


def net_post_json(url, body, headers=None, timeout=12):
    """POST a JSON body, return (status, text). HTTP errors are returned,
    not raised, so callers can read Google's error payloads (the youtube.com
    OAuth endpoint reports failures as 200/400 with a JSON error field)."""
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json",
            "User-Agent": TV_USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_ctx))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return int(e.code), e.read().decode("utf-8", "replace")
        except Exception:
            return int(e.code), ""
    except Exception as e:
        return -1, str(e)


def _try_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


# ----------------------------------------------------------------------------
# v0.3.7: the TV app session context (SmartTube's AppInfo).
#
# YouTube now de-personalises innertube browse for requests that don't look
# like the real TV app: the response is a 99 KB UI shell (search bar +
# feedNudgeRenderer sign-in nudge) with ZERO videoId entries - exactly what
# the v3.6 device logs showed for the account home/subscriptions/history
# ("account watch history empty", "subs feed failed -> RSS fallback").
#
# SmartTube survives by sending, on every /browse call:
#   * visitorData in the body AND the X-Goog-Visitor-Id header - their own
#     comment: "Empty Home fix (anonymous user) and improve Recommendations
#     for everyone" (RetrofitOkHttpHelper.kt);
#   * the full TV client context: tvAppInfo{appQuality, zylonLeftNav},
#     webpSupport/animatedWebpSupport, clientScreen, userAgent (a Cobalt
#     Fire-TV UA), acceptLanguage/acceptRegion, utcOffsetMinutes, the
#     context.user safety-mode block and racyCheckOk/contentCheckOk;
#   * the CURRENT TV client version, scraped from youtube.com/tv;
#   * X-Goog-Pageid when the account has a channel (brand identity).
# All of that is replicated here.
# ----------------------------------------------------------------------------
_TV_APP_INFO = {"ts": 0.0, "visitor": "", "version": "", "key": ""}
_TV_INFO_LOCK = threading.Lock()


def _utc_offset_minutes():
    """Local UTC offset in minutes as a string (SmartTube sends this)."""
    try:
        off = -time.altzone if (time.localtime().tm_isdst and time.daylight) \
            else -time.timezone
        return str(int(off // 60))
    except Exception:
        return "0"


def get_tv_app_info(max_age=86400, force=False):
    """visitorData + live TV client version from youtube.com/tv.

    SmartTube's AppInfo parser extracts exactly these (visitorData regex on
    the TV page HTML). Cached for a day; refreshed on demand. Never raises;
    returns whatever is known (possibly empty) - the request builders simply
    omit the fields when unavailable."""
    with _TV_INFO_LOCK:
        if not force and _TV_APP_INFO["visitor"] and \
                (time.time() - _TV_APP_INFO["ts"]) < max_age:
            return dict(_TV_APP_INFO)
    try:
        raw = net_get("https://www.youtube.com/tv", timeout=12,
                      headers={"User-Agent": TV_USER_AGENT,
                               "Referer": "https://www.youtube.com/tv"})
        html = raw.decode("utf-8", "replace")
        m = re.search(r'"visitorData"\s*:\s*"(.*?)"', html)
        visitor = m.group(1) if m else ""
        mv = re.search(
            r'"(?:clientVersion|innertubeContextClientVersion)"\s*:\s*"([\d.]+)"',
            html)
        version = mv.group(1) if mv else ""
        mk = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', html)
        key = mk.group(1) if mk else ""
        if visitor:
            with _TV_INFO_LOCK:
                _TV_APP_INFO.update(ts=time.time(), visitor=visitor,
                                    version=version, key=key)
    except Exception:
        pass
    with _TV_INFO_LOCK:
        return dict(_TV_APP_INFO)


def tv_visitor_data():
    """Current visitorData string ('' when the /tv page is unreachable)."""
    return get_tv_app_info().get("visitor") or ""


# ----------------------------------------------------------------------------
# v3.6 connectivity probe (the WiFi reconnect watchdog's heartbeat).
# A tiny HEAD against YouTube's own captive-portal endpoint: 204 within
# the timeout means the internet (not just the wifi interface) is up.
# ----------------------------------------------------------------------------
def probe_online(timeout=4, proxy="", prefer_ipv4=False):
    """True when the internet answers. Never raises, never blocks >2*timeout
    (two candidate endpoints are tried)."""
    for url in ("https://www.youtube.com/generate_204",
                "https://www.google.com/generate_204"):
        try:
            req = urllib.request.Request(url, method="HEAD", headers={
                "User-Agent": WEB_USER_AGENT})
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=_ssl_ctx))
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler(
                        {"http": proxy, "https": proxy}),
                    urllib.request.HTTPSHandler(context=_ssl_ctx))
            with opener.open(req, timeout=timeout) as resp:
                if int(resp.status) in (200, 204):
                    return True
        except urllib.error.HTTPError as e:
            # ANY HTTP answer means we reached the internet (404/301 from a
            # captive portal still means "online-ish"); only transport
            # errors (DNS, refused, timeout) mean offline
            if int(e.code) < 500:
                return True
        except Exception:
            continue
    return False


def net_looks_offline(exc):
    """Does this exception look like a connectivity failure (v3.6)?"""
    if exc is None:
        return False
    text = str(exc).lower()
    return any(k in text for k in (
        "timed out", "timeout", "temporary failure in name resolution",
        "name or service not known", "connection refused",
        "connection reset", "network is unreachable",
        "no route to host", "connection aborted", "getaddrinfo failed",
        "ssl", "eof occurred", "remote end closed"))


def ytdlp_common_args(prefs, for_video=False):
    """Shared yt-dlp CLI args derived from user preferences (route options)."""
    args = [
        "--no-warnings",
        "--no-check-certificates",
        "--ignore-errors",
        "--no-progress",
    ]
    timeout = prefs.get("socket_timeout", "15")
    try:
        if int(timeout) > 0:
            args += ["--socket-timeout", timeout]
    except ValueError:
        args += ["--socket-timeout", "15"]
    proxy = prefs.get("proxy", "")
    if proxy:
        args += ["--proxy", proxy]
    route = prefs.get("route", "Default")
    if prefs.get("prefer_ipv4", "Off") == "On" or route == "Cronet":
        # Cronet route mimics Chromium's Happy Eyeballs: prefer IPv4 on
        # handheld wifi stacks where IPv6 routing is often broken
        args.append("--force-ipv4")
    # v3.6: request YouTube metadata in the user's language. Without this
    # YouTube auto-translates search/feed titles to English (hl=en default)
    # and even fabricates garbage titles ("Minecraft1895", "Friday0828")
    # for translated videos - verified from the device logs.
    lang = YTDLP_LANG.get(prefs.get("language", "English"), "")
    if lang:
        args += ["--extractor-args", "youtube:lang=%s" % lang]
    if for_video:
        client = prefs.get("player_client", "Default")
        if client and client != "Default":
            args += ["--extractor-args", "youtube:player_client=%s" % client]
    return args


# ----------------------------------------------------------------------------
# Subscriptions (RSS) - SmartTube-style local, account-free subscriptions
# ----------------------------------------------------------------------------
SUBS_FILE = os.path.join(SCRIPT_DIR, "yt_subs.json")
SEEN_FILE = os.path.join(SCRIPT_DIR, "yt_seen.json")

RSS_NAMESPACES = {
    "a": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def _rss_field(entry, path):
    el = entry.find(path, RSS_NAMESPACES)
    return el.text if el is not None and el.text else ""


_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def _xml_unescape(text):
    if "&" not in text:
        return text

    def _sub(m):
        ent = m.group(1)
        if ent in _ENTITIES:
            return _ENTITIES[ent]
        if ent.startswith("#"):
            try:
                return chr(int(ent[1:]))
            except Exception:
                pass
        return m.group(0)

    return re.sub(r"&(#?[0-9a-zA-Z]+);", _sub, text)


def _parse_rss_et(root, channel_name):
    """ElementTree-based RSS parsing (preferred path)."""
    if channel_name == "":
        author = root.find(".//a:author/a:name", RSS_NAMESPACES)
        if author is not None and author.text:
            channel_name = author.text
    items = []
    for entry in root.findall("a:entry", RSS_NAMESPACES):
        vid = _rss_field(entry, "yt:videoId")
        title = _rss_field(entry, "a:title")
        if not vid or not title:
            continue
        published = _rss_field(entry, "a:published")
        views = 0
        likes = 0
        stats = entry.find(".//media:community/media:statistics", RSS_NAMESPACES)
        if stats is not None:
            try:
                views = int(stats.get("views", "0"))
            except ValueError:
                views = 0
        rating = entry.find(".//media:community/media:starRating", RSS_NAMESPACES)
        if rating is not None:
            try:
                likes = int(rating.get("count", "0"))
            except ValueError:
                likes = 0
        thumb = ""
        thumb_el = entry.find(".//media:group/media:thumbnail", RSS_NAMESPACES)
        if thumb_el is not None and thumb_el.get("url"):
            thumb = thumb_el.get("url")
        # upload_date as yyyymmdd (same format yt-dlp uses)
        upload_date = ""
        try:
            if published:
                # 2026-08-19T12:34:56+00:00
                d = published[:10].replace("-", "")
                if len(d) == 8:
                    upload_date = d
        except Exception:
            upload_date = ""
        items.append({
            "id": vid,
            "title": title,
            "channel": channel_name,
            "channel_id": _rss_field(entry, "yt:channelId"),
            "duration": None,          # RSS carries no duration
            "view_count": views,
            "like_count": likes,
            "upload_date": upload_date,
            "published_ts": _iso_to_ts(published),
            "thumbnail": thumb or ("https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid),
            "url": "https://www.youtube.com/watch?v=%s" % vid,
            "source": "rss",
        })
    items.sort(key=lambda v: v.get("published_ts") or 0, reverse=True)
    return items


def _parse_rss_fallback(xml_text, channel_name=""):
    """Regex-based RSS parser used when xml.etree is unavailable.

    Extracts exactly the same fields as the ElementTree path so the rest of
    the app cannot tell the difference.
    """
    entries = re.findall(r"<entry\b.*?</entry>", xml_text, re.S)
    if not entries:
        return []
    if channel_name == "":
        m = re.search(r"<author\b.*?<name>(.*?)</name>", xml_text, re.S)
        if m:
            channel_name = _xml_unescape(m.group(1).strip())
    items = []
    for chunk in entries:
        def _tag(name):
            m = re.search(r"<%s>(.*?)</%s>" % (name, name), chunk, re.S)
            return _xml_unescape(m.group(1).strip()) if m else ""

        vid = _tag("yt:videoId")
        title = _tag("title")
        if not vid or not title:
            continue
        published = _tag("published")
        views = 0
        m = re.search(r'<media:statistics[^>]*\bviews="(\d+)"', chunk)
        if m:
            views = int(m.group(1))
        likes = 0
        m = re.search(r'<media:starRating[^>]*\bcount="(\d+)"', chunk)
        if m:
            likes = int(m.group(1))
        thumb = ""
        m = re.search(r'<media:thumbnail[^>]*\burl="([^"]+)"', chunk)
        if m:
            thumb = _xml_unescape(m.group(1))
        upload_date = ""
        if len(published) >= 10:
            d = published[:10].replace("-", "")
            if len(d) == 8:
                upload_date = d
        items.append({
            "id": vid,
            "title": title,
            "channel": channel_name,
            "channel_id": _tag("yt:channelId"),
            "duration": None,
            "view_count": views,
            "like_count": likes,
            "upload_date": upload_date,
            "published_ts": _iso_to_ts(published),
            "thumbnail": thumb or ("https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid),
            "url": "https://www.youtube.com/watch?v=%s" % vid,
            "source": "rss",
        })
    items.sort(key=lambda v: v.get("published_ts") or 0, reverse=True)
    return items


def parse_rss_feed(xml_bytes, channel_name=""):
    """Parse a YouTube channel RSS feed into a list of video dicts.

    Uses ElementTree when available and falls back to a regex parser on
    firmwares whose Python ships without xml.etree (or on malformed feeds).
    """
    if HAS_ET:
        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            root = None
        if root is not None:
            return _parse_rss_et(root, channel_name)
    try:
        if isinstance(xml_bytes, bytes):
            text = xml_bytes.decode("utf-8", "replace")
        else:
            text = str(xml_bytes or "")
    except Exception:
        return []
    return _parse_rss_fallback(text, channel_name)


def _iso_to_ts(iso_str):
    try:
        from datetime import datetime
        base = iso_str[:19]
        dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
        return time.mktime(dt.timetuple())
    except Exception:
        return 0


class SubscriptionsManager(object):
    """Local subscriptions stored in yt_subs.json, fed by RSS."""

    def __init__(self, prefs):
        self.prefs = prefs
        self.channels = []       # [{channel_id, name, added}]
        self.seen_ids = set()    # video ids the user has already seen
        self.last_refresh = 0
        self.feed = []           # merged feed of dicts
        self._load()
        self._seen_dirty = False

    # -- persistence ---------------------------------------------------------
    def _load(self):
        try:
            if os.path.exists(SUBS_FILE):
                with open(SUBS_FILE, "r", encoding="utf-8") as f:
                    self.channels = json.load(f)
        except Exception:
            self.channels = []
        try:
            if os.path.exists(SEEN_FILE):
                with open(SEEN_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ids = data.get("ids", []) if isinstance(data, dict) else data
                self.seen_ids = set(ids)
        except Exception:
            self.seen_ids = set()

    def save_channels(self):
        try:
            with open(SUBS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.channels, f, ensure_ascii=False)
        except Exception as e:
            print("subs save error: %s" % e)

    def save_seen(self):
        # keep the set bounded (most recent 800 ids)
        if len(self.seen_ids) > 800:
            self.seen_ids = set(list(self.seen_ids)[-800:])
        try:
            with open(SEEN_FILE, "w", encoding="utf-8") as f:
                json.dump({"ids": list(self.seen_ids)}, f)
        except Exception as e:
            print("seen save error: %s" % e)

    # -- subscription management ----------------------------------------------
    def is_subscribed(self, channel_id):
        return any(c.get("channel_id") == channel_id for c in self.channels)

    def subscribe(self, channel_id, name):
        if not channel_id or self.is_subscribed(channel_id):
            return False
        self.channels.append({
            "channel_id": channel_id,
            "name": name or channel_id,
            "added": int(time.time()),
        })
        self.save_channels()
        self.last_refresh = 0   # force refresh
        return True

    def unsubscribe(self, channel_id):
        before = len(self.channels)
        self.channels = [c for c in self.channels if c.get("channel_id") != channel_id]
        if len(self.channels) != before:
            self.save_channels()
            return True
        return False

    # -- feed -----------------------------------------------------------------
    def fetch_channel(self, channel):
        """Fetch one channel RSS. Returns list of video dicts (may be empty)."""
        cid = channel.get("channel_id", "")
        if not cid:
            return []
        url = "https://www.youtube.com/feeds/videos.xml?channel_id=%s" % urllib.parse.quote(cid)
        try:
            raw = net_get(
                url,
                timeout=int(self.prefs.get("socket_timeout", "15") or 15),
                proxy=self.prefs.get("proxy", ""),
                prefer_ipv4=self.prefs.get("prefer_ipv4", "Off") == "On",
            )
            return parse_rss_feed(raw, channel.get("name", ""))
        except Exception as e:
            print("RSS fetch fail %s: %s" % (cid, e))
            return []

    def refresh(self, max_workers=6):
        """Fetch all subscribed channels in parallel, merge by date."""
        if not self.channels:
            self.feed = []
            self.last_refresh = time.time()
            return self.feed
        merged = []
        if HAS_CONCURRENT:
            with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(self.channels)))) as ex:
                for res in ex.map(self.fetch_channel, self.channels):
                    merged.extend(res)
        else:
            # sequential fallback when concurrent.futures is unavailable
            for ch in self.channels:
                merged.extend(self.fetch_channel(ch))
        merged.sort(key=lambda v: v.get("published_ts") or 0, reverse=True)
        # cap feed length
        self.feed = merged[:120]
        self.last_refresh = time.time()
        return self.feed

    def mark_seen(self, video_ids):
        for vid in video_ids:
            self.seen_ids.add(vid)
        self.save_seen()

    def is_new(self, video_id):
        return video_id not in self.seen_ids


# ----------------------------------------------------------------------------
# SponsorBlock - crowd-sourced in-video segment skipping
# ----------------------------------------------------------------------------
SB_CATEGORIES = [
    "sponsor", "selfpromo", "interaction", "intro", "outro",
    "preview", "highlight", "filler", "music_offtopic",
]

SB_PRESETS = {
    "Off": [],
    "Minimal": ["sponsor"],
    "Core": ["sponsor", "selfpromo", "intro"],
    "All": SB_CATEGORIES,
}

SB_LABELS = {
    "sponsor": "sponsor", "selfpromo": "self-promo", "interaction": "interaction",
    "intro": "intro", "outro": "outro", "preview": "preview",
    "highlight": "highlight", "filler": "filler", "music_offtopic": "non-music",
}


class SponsorBlockClient(object):
    """Fetches skip segments for a video (read-only, keyless API)."""

    def __init__(self, prefs):
        self.prefs = prefs

    def get_categories(self):
        return SB_PRESETS.get(self.prefs.get("sponsorblock", "Core"), SB_PRESETS["Core"])

    def fetch_segments(self, video_id):
        cats = self.get_categories()
        if not cats or not video_id:
            return []
        server = self.prefs.get("sb_server", "https://sponsor.ajay.app").rstrip("/")
        cats_json = json.dumps(cats)
        url = ("%s/api/skipSegments?videoID=%s&categories=%s"
               % (server, urllib.parse.quote(video_id), urllib.parse.quote(cats_json)))
        try:
            data = net_get_json(
                url,
                timeout=int(self.prefs.get("socket_timeout", "15") or 15),
                proxy=self.prefs.get("proxy", ""),
                prefer_ipv4=self.prefs.get("prefer_ipv4", "Off") == "On",
            )
            segments = []
            for seg in data or []:
                try:
                    category = seg.get("category", "")
                    pair = seg.get("segment", [0, 0])
                    start = float(pair[0])
                    end = float(pair[1])
                    if category in cats and end > start:
                        segments.append({
                            "category": category,
                            "label": SB_LABELS.get(category, category),
                            "start": start,
                            "end": end,
                        })
                except (TypeError, ValueError, IndexError):
                    continue
            # merge overlapping segments, drop tiny ones (<1s)
            segments = [s for s in segments if (s["end"] - s["start"]) >= 1.0]
            segments.sort(key=lambda s: s["start"])
            merged = []
            for seg in segments:
                if merged and seg["start"] <= merged[-1]["end"] + 0.5:
                    if seg["end"] > merged[-1]["end"]:
                        merged[-1]["end"] = seg["end"]
                else:
                    merged.append(seg)
            return merged
        except Exception as e:
            print("SponsorBlock fetch fail: %s" % e)
            return []


# ----------------------------------------------------------------------------
# DeArrow - clickbait-free titles (optional)
# ----------------------------------------------------------------------------
class DeArrowClient(object):
    def __init__(self, prefs):
        self.prefs = prefs
        self.cache = {}   # video_id -> better title (or None)

    def fetch_title(self, video_id):
        """Best DeArrow title for a video, or None to keep the original.

        v3.6 FIX: the /api/branding response is a DICT
        ({"titles": [...], "thumbnails": [...]}), NOT a list - the old
        list iteration hit AttributeError on the string keys and ALWAYS
        returned None (DeArrow was silently dead). Videos without any
        submission answer HTTP 404 - also None (original title)."""
        if not video_id:
            return None
        if video_id in self.cache:
            return self.cache[video_id]
        server = self.prefs.get("sb_server", "https://sponsor.ajay.app").rstrip("/")
        url = "%s/api/branding?videoID=%s" % (server, urllib.parse.quote(video_id))
        title = None
        try:
            data = net_get_json(url, timeout=8,
                                proxy=self.prefs.get("proxy", ""),
                                prefer_ipv4=self.prefs.get("prefer_ipv4", "Off") == "On")
            entries = []
            if isinstance(data, dict):
                entries = data.get("titles") or []
            elif isinstance(data, list):
                entries = data
            # best = locked submission with the most votes; a lone
            # "original" flag entry just confirms the real title
            best = None
            for e in entries:
                if not isinstance(e, dict):
                    continue
                t = e.get("title")
                if not t or not str(t).strip():
                    continue
                t = str(t).strip()
                if t.lower() == str(video_id).lower():
                    continue      # videoId-as-title garbage guard
                score = (1 if e.get("locked") else 0,
                         int(e.get("votes") or 0))
                if best is None or score > best[0]:
                    best = (score, t)
            if best is not None:
                title = best[1]
        except Exception:
            title = None
        self.cache[video_id] = title
        return title


# ----------------------------------------------------------------------------
# Return YouTubeDislike
# ----------------------------------------------------------------------------
class RYDClient(object):
    def __init__(self, prefs):
        self.prefs = prefs

    def fetch_votes(self, video_id):
        if not video_id:
            return None
        url = "https://returnyoutubedislikeapi.com/votes?videoId=%s" % urllib.parse.quote(video_id)
        try:
            data = net_get_json(url, timeout=8,
                                proxy=self.prefs.get("proxy", ""),
                                prefer_ipv4=self.prefs.get("prefer_ipv4", "Off") == "On")
            return {
                "likes": data.get("likes", 0) or 0,
                "dislikes": data.get("dislikes", 0) or 0,
            }
        except Exception:
            return None


# ----------------------------------------------------------------------------
# Search suggestions (keyless Google endpoint) + search history
# ----------------------------------------------------------------------------
SEARCHES_FILE = os.path.join(SCRIPT_DIR, "yt_searches.json")


def fetch_suggestions(query, prefs):
    """Return up to 6 suggestion strings for a partial query."""
    query = (query or "").strip()
    if len(query) < 2:
        return []
    url = ("https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q=%s"
           % urllib.parse.quote(query))
    try:
        data = net_get_json(url, timeout=5,
                            proxy=prefs.get("proxy", ""),
                            prefer_ipv4=prefs.get("prefer_ipv4", "Off") == "On")
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
            out = []
            for s in data[1][:6]:
                if isinstance(s, str) and s.strip():
                    out.append(s.strip())
            return out
    except Exception:
        pass
    return []


def load_search_history():
    try:
        if os.path.exists(SEARCHES_FILE):
            with open(SEARCHES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_search_history(items):
    try:
        with open(SEARCHES_FILE, "w", encoding="utf-8") as f:
            json.dump(items[:12], f, ensure_ascii=False)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Account login (SmartTube TV device-code flow) - v3.3
# ----------------------------------------------------------------------------
OAUTH_TOKEN_FILE = os.path.join(SCRIPT_DIR, "oauth_token.json")


class OAuthError(Exception):
    """Login/refresh failure with a machine-readable kind."""
    def __init__(self, message, kind="error"):
        Exception.__init__(self, message)
        self.kind = kind


def _it_text(node):
    """Extract text from an innertube text node (runs / simpleText / content)."""
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node.get("simpleText") or "")
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(str(r.get("text") or "") for r in runs if isinstance(r, dict))
    if "content" in node:
        return str(node.get("content") or "")
    return ""


def _parse_hms(text):
    """'1:23:45' / '12:34' / '1:23:45' -> seconds (0 when unparseable)."""
    try:
        parts = [int(p) for p in str(text).strip().split(":")]
    except (TypeError, ValueError):
        return 0
    if not parts or any(p < 0 for p in parts):
        return 0
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs


def _parse_views(text):
    digits = re.sub(r"[^\d]", "", str(text or "").split(" ")[0])
    try:
        return int(digits) if digits else 0
    except ValueError:
        return 0


def _walk_nodes(node, pred, out=None, depth=0):
    """Recursively collect dicts for which pred(dict) is True."""
    if out is None:
        out = []
    if depth > 40 or not isinstance(node, (dict, list)):
        return out
    if isinstance(node, dict):
        if pred(node):
            out.append(node)
        for v in node.values():
            _walk_nodes(v, pred, out, depth + 1)
    else:
        for v in node:
            _walk_nodes(v, pred, out, depth + 1)
    return out


def _thumb_from(node):
    """Best thumbnail URL from a innertube thumbnail node."""
    if isinstance(node, dict):
        for key in ("thumbnails",):
            lst = node.get(key)
            if isinstance(lst, list) and lst:
                for t in reversed(lst):
                    if isinstance(t, dict) and t.get("url"):
                        return str(t["url"])
        for v in node.values():
            u = _thumb_from(v)
            if u:
                return u
    return ""


def _parse_video_renderers(response):
    """Tolerant video-item extraction from any innertube browse response.

    Walks the whole JSON tree for dicts that look like video items (legacy
    renderers: videoRenderer / gridVideoRenderer / playlistVideoRenderer ...
    plus the newer lockup view models) so layout differences between the
    TV/web/feed variants never break the parser."""
    items = []
    seen = set()

    def _has_video_id(d):
        return isinstance(d.get("videoId"), str) and d.get("videoId")

    for d in _walk_nodes(response, _has_video_id):
        vid = d["videoId"]
        if vid in seen:
            continue
        title = _it_text(d.get("title")) or _it_text(d.get("headline"))
        byline = (d.get("shortBylineText") or d.get("ownerText") or
                  d.get("longBylineText") or d.get("bylineText"))
        channel = _it_text(byline)
        channel_id = ""
        for sub in _walk_nodes(byline, lambda x: isinstance(x.get("browseEndpoint"), dict)):
            cid = (sub.get("browseEndpoint") or {}).get("browseId", "")
            if isinstance(cid, str) and cid.startswith("UC"):
                channel_id = cid
                break
        length = _parse_hms(_it_text(d.get("lengthText")))
        if not length and d.get("thumbnailOverlays"):
            ov = _walk_nodes(d.get("thumbnailOverlays"),
                             lambda x: "thumbnailOverlayTimeStatusRenderer" in x)
            if ov:
                length = _parse_hms(_it_text(
                    ov[0].get("thumbnailOverlayTimeStatusRenderer")))
        view_text = _it_text(d.get("viewCountText") or d.get("shortViewCountText"))
        # v3.6 FIX: rows with NO title, NO channel and NO length are UI junk
        # (search bars, nudges, promo tiles), NOT videos - the old code
        # turned them into list entries with the VIDEO ID as the title
        # ("6yMrnC3jBmc" showing instead of a real title).
        if not title and not channel and not length:
            continue
        item = {
            "id": vid,
            # v3.6: keep "" when the title is unparseable - VideoItem shows
            # a neutral "Unknown" instead of the raw video ID.
            "title": title or "",
            "channel": channel or "",
            "channel_id": channel_id,
            "duration": length if length else None,
            "view_count": _parse_views(view_text),
            "upload_date": "",
            "thumbnail": _thumb_from(d.get("thumbnail") or d.get("thumbnails")),
            "is_live": bool("watching" in view_text.lower()) and not length,
            "source": "account",
        }
        if "watching" in view_text.lower() and not length:
            item["live_status"] = "is_live"
        seen.add(vid)
        items.append(item)

    # newer view-model lockups (contentId instead of videoId)
    def _is_lockup(d):
        lock = d.get("lockupViewModel")
        return isinstance(lock, dict) and isinstance(lock.get("contentId"), str)

    for d in _walk_nodes(response, _is_lockup):
        lock = d["lockupViewModel"]
        vid = lock.get("contentId")
        if not vid or vid in seen:
            continue
        meta = {}
        for sub in _walk_nodes(lock, lambda x: "lockupMetadataViewModel" in x):
            meta = sub.get("lockupMetadataViewModel") or {}
            break
        title = _it_text(meta.get("title"))
        if not title:
            continue
        items.append({
            "id": vid, "title": title, "channel": "", "channel_id": "",
            "duration": None, "view_count": 0, "upload_date": "",
            "thumbnail": _thumb_from(lock), "source": "account",
        })
        seen.add(vid)

    # v0.3.7: the CURRENT TV browse format - tileRenderer video cards.
    #
    # YouTube moved the TV feeds (home/subscriptions/history/channel) from
    # gridVideoRenderer to 'tileRenderer' view models: the video id sits in
    # contentId / onSelectCommand.watchEndpoint, the title in
    # metadata.tileMetadataRenderer, the thumbnail + duration in
    # header.tileHeaderRenderer. The classic walkers above find ONLY the
    # watchEndpoint dicts (no title/length -> skipped as junk) which made
    # every TV-format feed parse to ZERO items even when YouTube delivered
    # a full shelf of videos.
    for d in _walk_nodes(response,
                         lambda x: isinstance(x.get("tileRenderer"), dict)):
        t = d["tileRenderer"]
        ctype = str(t.get("contentType") or "")
        vid = t.get("contentId")
        if not (isinstance(vid, str) and len(vid) == 11):
            # playlists/other tiles carry non-video ids - try the command
            # (only a real 11-char video id counts)
            vid = ""
            for we in _walk_nodes(t.get("onSelectCommand") or {},
                                  lambda x: isinstance(x.get("watchEndpoint"), dict)):
                wv = (we.get("watchEndpoint") or {}).get("videoId")
                if isinstance(wv, str) and len(wv) == 11:
                    vid = wv
                    break
        if not vid or vid in seen:
            continue
        # title + text lines
        meta = {}
        for sub in _walk_nodes(t.get("metadata") or {},
                               lambda x: "tileMetadataRenderer" in x):
            meta = sub.get("tileMetadataRenderer") or {}
            break
        title = _it_text(meta.get("title"))
        channel = ""
        view_text = ""
        lines = meta.get("lines")
        if isinstance(lines, list):
            texts = []
            for line in lines:
                lt = ""
                for item in _walk_nodes(line, lambda x: "lineItemRenderer" in x):
                    txt = _it_text((item.get("lineItemRenderer") or {}).get("text"))
                    if txt:
                        lt = txt
                        break
                texts.append(lt)
            if texts:
                channel = texts[0]
            if len(texts) > 1:
                view_text = texts[1]
        # thumbnail + duration from the tile header
        header = {}
        for sub in _walk_nodes(t.get("header") or {},
                               lambda x: "tileHeaderRenderer" in x):
            header = sub.get("tileHeaderRenderer") or {}
            break
        length = 0
        live_overlay = False
        for ov in _walk_nodes(header.get("thumbnailOverlays") or [],
                               lambda x: "thumbnailOverlayTimeStatusRenderer" in x):
            ot = ov.get("thumbnailOverlayTimeStatusRenderer") or {}
            length = _parse_hms(_it_text(ot.get("text")))
            # LIVE-style overlay marks live tiles regardless of language
            # ("AO VIVO"/"LIVE"/"В ЭФИРЕ" + the watching-view line below)
            if str(ot.get("style") or "").upper() == "LIVE" and not length:
                live_overlay = True
            break
        if not title and not channel and not length:
            continue          # pure UI tile (menu rows etc.)
        if live_overlay or ("watching" in (view_text or "").lower() and not length):
            is_live = True
        else:
            is_live = False
        item = {
            "id": vid,
            "title": title or "",
            "channel": channel or "",
            "channel_id": "",
            "duration": length if length else None,
            "view_count": _parse_views(view_text),
            "upload_date": "",
            "thumbnail": _thumb_from(header.get("thumbnail")),
            "is_live": is_live,
            "source": "account",
        }
        if is_live:
            item["live_status"] = "is_live"
        seen.add(vid)
        items.append(item)
    return items


def _parse_channel_renderers(response):
    """Tolerant channel extraction for subscription import.

    v0.3.7: also understands the CURRENT TV shapes - guideEntryRenderer
    entries from the /guide navigation (SmartTube's getSubscribedChannels
    path: title + navigationEndpoint.browseEndpoint.browseId) and channel
    tileRenderers (contentId = UC...). The old FEchannels response moved
    to those formats, so the direct-channelId walker alone found nothing."""
    out = []
    seen = set()

    def _is_channel(d):
        cid = d.get("channelId")
        return isinstance(cid, str) and cid.startswith("UC") and (
            d.get("title") or d.get("displayName"))

    for d in _walk_nodes(response, _is_channel):
        cid = d["channelId"]
        if cid in seen:
            continue
        name = _it_text(d.get("title")) or _it_text(d.get("displayName")) \
            or _it_text(d.get("channelName"))
        if name:
            seen.add(cid)
            out.append({"channel_id": cid, "name": name})

    # v0.3.7: guide entries (TV /guide navigation - the subscriptions row)
    for d in _walk_nodes(response, lambda x: "guideEntryRenderer" in x):
        ge = d.get("guideEntryRenderer") or {}
        cid = ""
        for be in _walk_nodes(ge.get("navigationEndpoint") or {},
                              lambda x: isinstance(x.get("browseEndpoint"), dict)):
            bid = (be.get("browseEndpoint") or {}).get("browseId", "")
            if isinstance(bid, str) and bid.startswith("UC"):
                cid = bid
                break
        name = _it_text(ge.get("formattedTitle")) or _it_text(ge.get("title")) \
            or str(ge.get("title") or "")
        if cid and name and cid not in seen:
            seen.add(cid)
            out.append({"channel_id": cid, "name": name})

    # v0.3.7: channel tiles (TV tile format, TILE_CONTENT_TYPE_CHANNEL)
    for d in _walk_nodes(response,
                         lambda x: isinstance(x.get("tileRenderer"), dict)):
        t = d["tileRenderer"]
        cid = t.get("contentId")
        if not (isinstance(cid, str) and cid.startswith("UC")):
            continue
        meta = {}
        for sub in _walk_nodes(t.get("metadata") or {},
                               lambda x: "tileMetadataRenderer" in x):
            meta = sub.get("tileMetadataRenderer") or {}
            break
        name = _it_text(meta.get("title"))
        if name and cid not in seen:
            seen.add(cid)
            out.append({"channel_id": cid, "name": name})
    return out


def _parse_accounts_list(response):
    """Account identity entries from youtubei account/accounts_list.

    v0.3.7: the page id lives at serviceEndpoint.selectActiveIdentityEndpoint.
    supportedTokens[*].pageIdToken.pageId (SmartTube's AccountInt JsonPath) -
    the old parser looked for a direct pageId/pageid field that never
    exists, so X-Goog-Pageid was NEVER sent and browse acted as no YouTube
    identity at all (feeds answered the anonymous shell). isSelected and
    hasChannel are extracted the same way."""
    out = []

    def _is_account(d):
        return "accountName" in d and isinstance(d.get("accountName"), (dict, str))

    def _pageid_of(d):
        # exact SmartTube path: serviceEndpoint.selectActiveIdentityEndpoint.
        #   supportedTokens[*].pageIdToken.pageId
        for ep in _walk_nodes(
                d, lambda x: isinstance(x.get("selectActiveIdentityEndpoint"), dict)):
            toks = ep["selectActiveIdentityEndpoint"].get("supportedTokens")
            if isinstance(toks, list):
                for t in toks:
                    if isinstance(t, dict):
                        pid = (t.get("pageIdToken") or {}).get("pageId")
                        if isinstance(pid, str) and pid:
                            return pid
        # generic fallback: any pageIdToken dict anywhere under the entry
        for tok in _walk_nodes(d, lambda x: isinstance(x.get("pageIdToken"), dict)):
            pid = (tok.get("pageIdToken") or {}).get("pageId")
            if isinstance(pid, str) and pid:
                return pid
        # legacy direct field
        pid = d.get("pageId") or d.get("pageid")
        return pid if isinstance(pid, str) and pid else ""

    for d in _walk_nodes(response, _is_account):
        entry = {
            "name": _it_text(d.get("accountName")) or "",
            "email": _it_text(d.get("accountByline")) or "",
            "avatar": _thumb_from(d.get("accountPhoto") or d.get("accountAvatar")
                                  or d.get("avatar")),
            "pageid": _pageid_of(d),
            "selected": bool(d.get("isSelected")),
            "has_channel": bool(d.get("hasChannel")),
            "channel": _it_text(d.get("channelHandle")) or "",
        }
        if entry["name"]:
            out.append(entry)
    return out


class YouTubeAuth(object):
    """Persistent YouTube account session (SmartTube's TV device-code flow).

    Token file: oauth_token.json next to the app (survives reboots; the
    refresh token is long-lived until revoked in Google account settings).
    The device id + client credentials are persisted alongside so a later
    Google client rotation can be handled by re-scraping base.js."""

    def __init__(self, token_file=None):
        self.token_file = token_file or OAUTH_TOKEN_FILE
        # RLock: ensure_access_token() holds it while calling
        # refresh_access_token(), which re-acquires it to apply the token.
        self._lock = threading.RLock()
        self.data = {}
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self):
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.data = data
        except Exception:
            self.data = {}

    def _save(self):
        try:
            with open(self.token_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
        except Exception as e:
            print("oauth token save error: %s" % e)

    # -- state ---------------------------------------------------------------
    def logged_in(self):
        return bool(self.data.get("refresh_token"))

    def account_name(self):
        return self.data.get("account_name") or ""

    def account_avatar(self):
        return self.data.get("account_avatar") or ""

    def sign_out(self):
        with self._lock:
            keep = self.data.get("device_id")
            self.data = {}
            if keep:
                self.data["device_id"] = keep
            self._save()

    def _client(self):
        return (self.data.get("client_id") or TV_CLIENT_ID,
                self.data.get("client_secret") or TV_CLIENT_SECRET)

    def _device_id(self):
        did = self.data.get("device_id")
        if not did:
            try:
                import uuid
                did = str(uuid.uuid4())
            except Exception:
                did = "pilastube-%d" % int(time.time() * 1000)
            self.data["device_id"] = did
            self._save()
        return did

    # -- client rotation (SmartTube's exact fallback) -------------------------
    def _scrape_tv_client(self):
        """Fetch youtube.com/tv and scrape the OAuth client from base.js -
        the same key-rotation survival mechanism SmartTube uses."""
        try:
            raw = net_get("https://www.youtube.com/tv", timeout=12,
                          headers={"User-Agent": TV_USER_AGENT})
            html = raw.decode("utf-8", "replace")
            m = re.search(r'id="base-js"[^>]*src="([^"]+)"', html) or \
                re.search(r'src="(/s/player/[^"]+base\.js[^"]*)"', html)
            if not m:
                return None
            base = m.group(1)
            if base.startswith("/"):
                base = "https://www.youtube.com" + base
            raw_js = net_get(base, timeout=20,
                             headers={"User-Agent": TV_USER_AGENT})
            js = raw_js.decode("utf-8", "replace")
            # first occurrence only - later ones carry a different (wrong) pair
            m2 = re.search(
                r'clientId:"([-\w]+\.apps\.googleusercontent\.com)",\n?[$\w]+:"(\w+)"',
                js)
            if m2:
                return m2.group(1), m2.group(2)
        except Exception as e:
            print("tv client scrape failed: %s" % e)
        return None

    def _renew_client(self):
        fresh = self._scrape_tv_client()
        if fresh:
            self.data["client_id"] = fresh[0]
            self.data["client_secret"] = fresh[1]
            self._save()
            return True
        return False

    # -- device-code login ----------------------------------------------------
    def request_device_code(self):
        """Step 1: ask YouTube for a user code. Returns the response dict
        (user_code / device_code / interval / expires_in / verification_url)."""
        cid, _ = self._client()
        body = {"client_id": cid, "device_id": self._device_id(),
                "model_name": TV_MODEL_NAME, "scope": TV_SCOPE}
        st, text = net_post_json(OAUTH_DEVICE_URL, body, timeout=12)
        j = _try_json(text)
        if j and j.get("device_code") and j.get("user_code"):
            return j
        err = (j or {}).get("error") or ""
        # rotated/rejected client credentials: re-scrape base.js, retry once
        if st in (400, 401, 403) or err in ("invalid_client", "restricted_client"):
            if self._renew_client():
                body["client_id"] = self.data["client_id"]
                st, text = net_post_json(OAUTH_DEVICE_URL, body, timeout=12)
                j = _try_json(text)
                if j and j.get("device_code") and j.get("user_code"):
                    return j
                err = (j or {}).get("error") or text[:120]
        raise OAuthError(err or ("HTTP %s" % st), kind=err or "http_error")

    def poll_token(self, device_code, interval=5, expires_at=None,
                   cancelled=None):
        """Step 2: block until the user authorises the code (or it expires).

        The youtube.com token endpoint reports 'authorization_pending' as a
        200 with an error body - handled here. Sleeps happen between polls so
        cancellation is checked at least once per interval."""
        interval = max(2, int(interval or 5))
        deadline = expires_at or (time.time() + 1800)
        bad_polls = 0
        while time.time() < deadline:
            if cancelled is not None and cancelled():
                raise OAuthError("cancelled", kind="cancelled")
            time.sleep(interval)
            if cancelled is not None and cancelled():
                raise OAuthError("cancelled", kind="cancelled")
            cid, sec = self._client()
            st, text = net_post_json(OAUTH_TOKEN_URL, {
                "code": device_code, "client_id": cid, "client_secret": sec,
                "grant_type": "http://oauth.net/grant_type/device/1.0"},
                timeout=12)
            j = _try_json(text)
            err = (j or {}).get("error") or ""
            if j and j.get("refresh_token"):
                with self._lock:
                    self._apply_token(j)
                return j
            if err in ("authorization_pending", "") and st in (200, 400, 428):
                bad_polls = 0
                continue
            if err == "slow_down":
                interval += 5
                bad_polls = 0
                continue
            if err in ("access_denied", "access_blocked"):
                raise OAuthError(err, kind="denied")
            if err == "expired_token":
                raise OAuthError(err, kind="expired")
            bad_polls += 1
            if bad_polls >= 3:
                raise OAuthError(err or ("HTTP %s" % st), kind=err or "http_error")
        raise OAuthError("expired_token", kind="expired")

    def _apply_token(self, j):
        if j.get("refresh_token"):
            self.data["refresh_token"] = j["refresh_token"]
        if j.get("access_token"):
            self.data["access_token"] = j["access_token"]
        try:
            self.data["access_token_expires"] = time.time() + \
                int(j.get("expires_in") or 3600) - 120
        except (TypeError, ValueError):
            self.data["access_token_expires"] = time.time() + 3480
        self._save()

    def _token_expired(self):
        exp = self.data.get("access_token_expires") or 0
        return time.time() >= float(exp)

    def refresh_access_token(self):
        """Exchange the stored refresh token for a fresh access token."""
        rt = self.data.get("refresh_token")
        if not rt:
            raise OAuthError("not signed in", kind="not_logged_in")
        cid, sec = self._client()
        for attempt in (0, 1):
            st, text = net_post_json(OAUTH_TOKEN_URL, {
                "refresh_token": rt, "client_id": cid, "client_secret": sec,
                "grant_type": "refresh_token"}, timeout=12)
            j = _try_json(text)
            if j and j.get("access_token"):
                with self._lock:
                    self._apply_token(j)
                return j["access_token"]
            err = (j or {}).get("error") or ""
            if err == "invalid_client" and attempt == 0 and self._renew_client():
                cid, sec = self._client()
                continue
            if err == "invalid_grant":
                raise OAuthError("revoked", kind="revoked")
            raise OAuthError(err or ("HTTP %s" % st), kind=err or "http_error")
        raise OAuthError("refresh failed", kind="http_error")

    def ensure_access_token(self):
        """Valid access token for innertube calls (refreshes when needed)."""
        if not self.logged_in():
            raise OAuthError("not signed in", kind="not_logged_in")
        tok = self.data.get("access_token")
        if tok and not self._token_expired():
            return tok
        with self._lock:
            tok = self.data.get("access_token")
            if tok and not self._token_expired():
                return tok
            return self.refresh_access_token()

    # -- authenticated innertube ----------------------------------------------
    def _innertube_headers(self, token):
        """SmartTube's exact innertube header set (RetrofitOkHttpHelper):
        Cobalt TV UA + Referer + visitor id + Bearer + page id."""
        hdrs = {"Authorization": "Bearer %s" % token,
                "User-Agent": TV_USER_AGENT,
                "Referer": "https://www.youtube.com/tv",
                "Origin": "https://www.youtube.com"}
        visitor = tv_visitor_data()
        if visitor:
            hdrs["X-Goog-Visitor-Id"] = visitor
        pageid = self.data.get("pageid")
        if pageid:
            hdrs["X-Goog-Pageid"] = pageid
        return hdrs

    def _tv_context(self, hl=None, gl=None):
        """v0.3.7: SmartTube's full TV browse template (BrowseApiHelper).

        The minimal v3.6 context (name/version/deviceModel/hl/gl) is exactly
        what YouTube now answers with a de-personalised UI shell: 200 OK,
        valid JSON, zero videos. The real TV app additionally identifies
        itself with tvAppInfo/zylon, the Cobalt user agent, a visitor id and
        the safety-mode blocks - replicated 1:1 below."""
        info = get_tv_app_info()
        client = {
            "clientName": "TVHTML5",
            "clientVersion": info.get("version") or TV_CLIENT_VERSION,
            "clientScreen": "WATCH",
            "userAgent": TV_USER_AGENT,
            "tvAppInfo": {"appQuality": "TV_APP_QUALITY_FULL_ANIMATION",
                          "zylonLeftNav": True},
            "webpSupport": False,
            "animatedWebpSupport": True,
            "acceptLanguage": hl or "en",
            "acceptRegion": gl or "US",
            "utcOffsetMinutes": _utc_offset_minutes(),
            "hl": hl or "en",
            "gl": gl or "US",
        }
        if info.get("visitor"):
            client["visitorData"] = info["visitor"]
        return {"context": {"client": client,
                            "user": {"enableSafetyMode": False,
                                     "lockedSafetyMode": False}},
                "racyCheckOk": True, "contentCheckOk": True}

    def accounts_list(self):
        """Account identity (name / avatar / brand-account page id).

        v0.3.7: asks for owner + BRAND accounts like SmartTube's
        accountReadMask - the brand entries carry the pageIdToken
        (serviceEndpoint.selectActiveIdentityEndpoint.supportedTokens)
        that browse needs as X-Goog-Pageid to act as the account's YouTube
        identity. Without it the feeds answer the anonymous shell."""
        token = self.ensure_access_token()
        body = self._tv_context()
        body["accountReadMask"] = {"returnOwner": True,
                                   "returnBrandAccounts": True,
                                   "returnPersonaAccounts": False}
        st, text = net_post_json(
            INNERTUBE_API + "/account/accounts_list",
            body, headers=self._innertube_headers(token),
            timeout=12)
        if st == 401:
            token = self.refresh_access_token()
            st, text = net_post_json(
                INNERTUBE_API + "/account/accounts_list",
                body, headers=self._innertube_headers(token),
                timeout=12)
        j = _try_json(text) or {}
        accounts = []
        if not j.get("error"):
            accounts = _parse_accounts_list(j)
        if not accounts:
            # belt and braces: the mask arg is rejected on some account
            # states - retry with the plain (v3.3-proven) body
            body.pop("accountReadMask", None)
            st, text = net_post_json(
                INNERTUBE_API + "/account/accounts_list",
                body, headers=self._innertube_headers(token),
                timeout=12)
            j = _try_json(text) or {}
            if not j.get("error"):
                accounts = _parse_accounts_list(j)
        if j.get("error"):
            raise OAuthError(str(j["error"])[:80], kind="innertube")
        if accounts:
            # pick the identity the feeds will act as: the selected one when
            # it carries a pageIdToken, else any brand/channel identity (the
            # YouTube presence that owns subscriptions/history), else the
            # selected one, else the first
            best = None
            for a in accounts:
                if a.get("selected") and a.get("pageid"):
                    best = a
                    break
            if best is None:
                for a in accounts:
                    if a.get("pageid"):
                        best = a
                        break
            if best is None:
                for a in accounts:
                    if a.get("selected"):
                        best = a
                        break
            if best is None:
                best = accounts[0]
            with self._lock:
                self.data["account_name"] = best.get("name") or ""
                self.data["account_avatar"] = best.get("avatar") or ""
                self.data["pageid"] = best.get("pageid") or ""
                self.data["account_has_channel"] = bool(
                    best.get("has_channel"))
                self._save()
        return accounts

    def browse(self, browse_id, timeout=15, hl=None, gl=None):
        """Authenticated innertube browse; returns the parsed JSON or raises.

        v0.3.7: SmartTube's request shape - no ?key= when a Bearer is present,
        Cobalt TV UA + Referer + X-Goog-Visitor-Id, full TV context. The
        response is logged (bytes + videoId count) so device logs show
        exactly what YouTube answered."""
        token = self.ensure_access_token()
        body = self._tv_context(hl=hl, gl=gl)
        body["browseId"] = browse_id
        url = INNERTUBE_API + "/browse?prettyPrint=false"
        st, text = net_post_json(url, body,
                                 headers=self._innertube_headers(token),
                                 timeout=timeout)
        if st == 401:
            token = self.refresh_access_token()
            st, text = net_post_json(url, body,
                                     headers=self._innertube_headers(token),
                                     timeout=timeout)
        j = _try_json(text)
        if j is None:
            raise OAuthError("browse response not JSON", kind="innertube")
        if isinstance(j, dict) and j.get("error"):
            raise OAuthError(str(j["error"])[:80], kind="innertube")
        try:
            _dbg = json.dumps(j)
            print("[browse] %s -> HTTP %s, %d bytes, %d videoIds, visitor=%s, "
                  "pageid=%s" % (browse_id, st, len(_dbg),
                                 _dbg.count('\"videoId\"'),
                                 "yes" if tv_visitor_data() else "no",
                                  "yes" if self.data.get("pageid") else "no"))
        except Exception:
            pass
        return j

    def fetch_account_feed(self, kind, hl="en", gl="US"):
        """The signed-in account's own feed: 'subscriptions', 'history' or
        'home' (SmartTube's personalised recommended feed).
        Returns a list of item dicts (VideoItem-compatible); empty list when
        the account feed is unavailable.

        v0.3.7: home = browseId 'default' (SmartTube's exact TV home query);
        FEwhat_to_watch kept as a fallback for account states where the
        'default' shell answers empty."""
        browse_id = {
            "subscriptions": "FEsubscriptions",
            "history": "FEhistory",
            "home": "default",
        }.get(kind, "FEsubscriptions")
        j = self.browse(browse_id, hl=hl, gl=gl)
        items = _parse_video_renderers(j)
        if not items and kind == "home" and browse_id != "FEwhat_to_watch":
            j = self.browse("FEwhat_to_watch", hl=hl, gl=gl)
            items = _parse_video_renderers(j)
        return items

    def fetch_account_channels(self):
        """The signed-in account's subscribed channels (for the local list).

        v0.3.7: the TV /guide navigation first (SmartTube's
        getSubscribedChannels path - the guide's subscriptions section is
        stable across the renderer migrations), then the FEchannels browse
        as the fallback."""
        channels = []
        try:
            body = self._tv_context()
            st, text = net_post_json(
                INNERTUBE_API + "/guide?prettyPrint=false", body,
                headers=self._innertube_headers(
                    self.ensure_access_token()), timeout=15)
            j = _try_json(text)
            if isinstance(j, dict) and not j.get("error"):
                channels = _parse_channel_renderers(j)
        except Exception:
            channels = []
        if channels:
            return channels
        j = self.browse("FEchannels")
        return _parse_channel_renderers(j)


def browse_keyless(browse_id, timeout=12, hl=None, gl=None):
    """Anonymous innertube browse (v3.6: WEB client, browser-like headers).

    Used for the Recommended tab on logged-out devices and for category
    feeds. Returns the parsed JSON dict or None on any failure.

    v3.6 REWRITE: the old TVHTML5-anonymous call is dead twice over:
      * the home feed browseId is now FEwhat_to_watch (was 400 "invalid
        argument" -> "home feeds unavailable" on every launch);
      * anonymous TV browse returns a search-bar + sign-in nudge with ZERO
        videos (verified from the sandbox) - keyless TV home does not
        exist. The WEB client with X-YouTube-Client headers answers like a
        real browser, so logged-out users get the real (region-locked)
        home feed instead of a silent fallback to trending."""
    client = {
        "clientName": "WEB",
        "clientVersion": WEB_CLIENT_VERSION,
        "hl": hl or "en",
        "gl": gl or "US",
    }
    body = {"context": {"client": client}, "browseId": browse_id}
    headers = {
        "User-Agent": WEB_USER_AGENT,
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": WEB_CLIENT_VERSION,
        "Origin": "https://www.youtube.com",
        "Referer": "https://www.youtube.com/",
    }
    try:
        st, text = net_post_json(
            INNERTUBE_API + "/browse", body, headers=headers, timeout=timeout)
        j = _try_json(text)
        if st == 200 and isinstance(j, dict) and not j.get("error"):
            return j
    except Exception:
        pass
    return None


def fetch_keyless_feed(browse_id, hl=None, gl=None):
    """Anonymous browse -> video items (empty list on failure)."""
    j = browse_keyless(browse_id, hl=hl, gl=gl)
    if j is None:
        return []
    return _parse_video_renderers(j)


_AUTH = None


def get_auth():
    """Process-wide YouTubeAuth singleton."""
    global _AUTH
    if _AUTH is None:
        _AUTH = YouTubeAuth()
    return _AUTH


# ----------------------------------------------------------------------------
# Format selection - codec preference (H.264/VP9/AV1), height cap, DASH pairing
# ----------------------------------------------------------------------------
QUALITY_HEIGHTS = {"Auto": 480, "240p": 240, "360p": 360, "480p": 480, "720p": 720, "1080p": 1080}

# v3.2: SmartTube-style video codec selector. H.264 is the safe default on
# the R36S (only codec with any chance of hardware decode); VP9/AV1 are
# software-decoded (heavier CPU, lower ceiling on usable resolution) but
# often ship at lower bitrates for the same quality.
CODEC_OPTIONS = ["Auto", "H.264", "VP9", "AV1"]


def codec_family(vcodec):
    """Map a yt-dlp vcodec string to one of CODEC_OPTIONS (or "")."""
    v = (vcodec or "").lower()
    if v.startswith("avc1") or v.startswith("h264") or v.startswith("avc3"):
        return "H.264"
    if v.startswith("vp9") or v.startswith("vp09"):
        return "VP9"
    if v.startswith("av01"):
        return "AV1"
    return ""


def quality_cap(quality):
    if quality == "Auto":
        return 480          # 640x480 screen: 480p is the sweet spot
    return QUALITY_HEIGHTS.get(quality, 480)


# ----------------------------------------------------------------------------
# v3.4: live-stream audio pairing - MASTER MANIFEST PARSING
#
# The v3.3 approach (swap /itag/140|141|139/ into the video manifest URL and
# probe) was WRONG: 140/141/139 are VOD DASH audio itags and do not exist in
# the live hls_playlist family. Device log, SBT Ao Vivo: probe returned
# nothing -> "audio=NO AUDIO TRACK" -> live video played silent.
#
# How YouTube live HLS actually works (and what SmartTube/mpv do):
#   * the MASTER manifest (hls_variant URL) lists the video-only variants
#     (#EXT-X-STREAM-INF) AND the audio group (#EXT-X-MEDIA:TYPE=AUDIO
#     with its own playlist URI) - the audio playlist URL is RIGHT THERE;
#   * some streams also expose MUXED variants (itags 91-96: video+audio in
#     one playlist).
# So: fetch the master (a 1-5 KB text file), parse the audio group URI and
# hand ffmpeg two inputs (video playlist + audio playlist). Fallbacks: a
# muxed variant as a single input, then an itag-swap probe verified with a
# 2-second ffmpeg run (checks for an "Audio:" stream).
# ----------------------------------------------------------------------------
LIVE_MUXED_SWAP_ITAGS = (94, 93, 95, 96, 92)
LIVE_AUDIO_SWAP_ITAGS = (230, 229, 231, 232, 233, 140, 141, 139)


def hls_parse_attrs(attr_text):
    """Parse an HLS attribute list: KEY=VALUE,KEY="quoted, value",... """
    attrs = {}
    i = 0
    s = attr_text
    n = len(s)
    while i < n:
        eq = s.find("=", i)
        if eq < 0:
            break
        key = s[i:eq].strip()
        j = eq + 1
        if j < n and s[j] == '"':
            k = s.find('"', j + 1)
            if k < 0:
                k = n
            val = s[j + 1:k]
            i = k + 1
        else:
            k = s.find(",", j)
            if k < 0:
                k = n
            val = s[j:k].strip()
            i = k
        if i < n and s[i] == ",":
            i += 1
        if key:
            attrs[key] = val
    return attrs


def parse_hls_master(text):
    """Split an HLS master manifest into (audio_groups, variants).

    audio_groups: list of attribute dicts (EXT-X-MEDIA:TYPE=AUDIO entries)
    variants: list of {"url":..., "attrs": {...}} from EXT-X-STREAM-INF.
    """
    audio_groups = []
    variants = []
    pending_attrs = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = hls_parse_attrs(line[len("#EXT-X-MEDIA:"):])
            if attrs.get("TYPE") == "AUDIO" and attrs.get("URI"):
                audio_groups.append(attrs)
        elif line.startswith("#EXT-X-STREAM-INF:"):
            pending_attrs = hls_parse_attrs(line[len("#EXT-X-STREAM-INF:"):])
        elif line and not line.startswith("#"):
            if pending_attrs is not None:
                variants.append({"url": line, "attrs": pending_attrs})
            pending_attrs = None
    return audio_groups, variants


def live_audio_from_master(master_url, timeout=6, want_lang=None):
    """Fetch the live MASTER manifest and return (audio_uri, variants).

    audio_uri is None when the master has no audio group (muxed-only stream
    or an unparseable response). Raises nothing - network errors return
    (None, []).

    v3.6 want_lang: pick the EXT-X-MEDIA audio rendition whose LANGUAGE
    attribute matches (prefix match, 'pt' == 'pt-BR'). 'Original'/None
    keeps the DEFAULT=YES rendition (YouTube marks the original track
    DEFAULT). The rendition list is also stashed globally for the player
    menu via live_master_audio_langs()."""
    if not master_url:
        return None, []
    try:
        raw = net_get(master_url, timeout=timeout,
                      headers={"User-Agent": "Lavf/61.7.100"})
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) \
            else str(raw)
    except Exception:
        return None, []
    if not text.lstrip().startswith("#EXTM3U"):
        return None, []
    audio_groups, variants = parse_hls_master(text)
    # v3.6: remember every audio rendition's language for the player menu
    rends = []
    for a in audio_groups:
        lang = (a.get("LANGUAGE") or "").strip()
        name = (a.get("NAME") or lang or "audio").strip()
        uri = a.get("URI") or ""
        try:
            uri_abs = urllib.parse.urljoin(master_url, uri) if uri else ""
        except Exception:
            uri_abs = uri
        rends.append((lang.lower() or None, name, uri_abs))
    try:
        global _LAST_MASTER_AUDIO_LANGS
        _LAST_MASTER_AUDIO_LANGS = rends
    except Exception:
        pass
    best = None
    if want_lang and want_lang != "Original":
        want = str(want_lang).split("-")[0].split(".")[0].lower()
        for a in audio_groups:
            lang = (a.get("LANGUAGE") or "").split("-")[0].split(".")[0] \
                .lower()
            if lang and lang == want and a.get("URI"):
                best = a
                break
    if best is None:
        for pri, key in (("DEFAULT", "YES"), ("AUTOSELECT", "YES")):
            for a in audio_groups:
                if a.get(pri) == key:
                    best = a
                    break
            if best is not None:
                break
    if best is None and audio_groups:
        best = audio_groups[0]
    audio_uri = best.get("URI") if best else None
    if audio_uri:
        # resolve relative playlist URIs against the master URL (ffmpeg
        # needs an absolute URL it can open)
        try:
            audio_uri = urllib.parse.urljoin(master_url, audio_uri)
        except Exception:
            pass
    return audio_uri, variants


_LAST_MASTER_AUDIO_LANGS = []


def live_master_audio_langs():
    """Audio renditions (lang, name, uri) of the last parsed live master."""
    try:
        return list(_LAST_MASTER_AUDIO_LANGS)
    except Exception:
        return []


def _variant_is_muxed(attrs):
    codecs = (attrs.get("CODECS") or "")
    has_audio = "mp4a" in codecs or "opus" in codecs
    has_video = ("avc1" in codecs or "avc3" in codecs or "h264" in codecs
                 or "vp09" in codecs or "vp9" in codecs or "av01" in codecs)
    return has_audio and has_video


def _live_swap_urls(video_url, itags):
    """Manifest URLs with /itag/NNN/ swapped to each candidate itag."""
    url = str(video_url or "")
    m = re.search(r"itag/(\d+)/", url)
    if not m or "hls" not in url:
        return []
    cur = m.group(1)
    out = []
    for itag in itags:
        if str(itag) != cur:
            out.append(url.replace("itag/%s/" % cur, "itag/%d/" % itag, 1))
    return out


def _ffmpeg_probe_streams(url, ffmpeg_path, timeout=8):
    """Run a tiny ffmpeg decode and report which stream kinds it found.

    Returns (has_audio, has_video, ok). Only used as the LAST-resort live
    audio fallback (the master parse above answers this for free)."""
    if not ffmpeg_path:
        return False, False, False
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "info",
             "-reconnect", "1", "-reconnect_streamed", "1",
             "-i", url, "-t", "0.5", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout)
        err = proc.stderr or ""
        return ("Audio:" in err, "Video:" in err, True)
    except Exception:
        return False, False, False


def probe_live_audio(video_url, master_url=None, ffmpeg_path=None):
    """v3.4: return (audio_url_or_None, muxed_url_or_None).

    Chain: master manifest audio group -> itag-swapped candidates verified
    with a 2s ffmpeg probe (audio-only first, then muxed)."""
    audio, variants = live_audio_from_master(master_url, timeout=6)
    if audio:
        return audio, None
    for cand in _live_swap_urls(video_url, LIVE_AUDIO_SWAP_ITAGS[:4]):
        has_a, _has_v, ok = _ffmpeg_probe_streams(cand, ffmpeg_path)
        if ok and has_a:
            # audio-only candidate (a muxed one would report video too, but
            # pairing it as the audio input would double the video decode)
            return cand, None
    for cand in _live_swap_urls(video_url, LIVE_MUXED_SWAP_ITAGS):
        has_a, has_v, ok = _ffmpeg_probe_streams(cand, ffmpeg_path)
        if ok and has_a and has_v:
            return None, cand
    return None, None


def LOG_LIVE_AUDIO(url):
    """Device-log marker: live audio paired."""
    try:
        print("[PLAYER] live audio paired (master manifest audio group ok)")
    except Exception:
        pass


def _live_hls_audio_hint(info):
    """Audio-only HLS formats the codec classifier missed.

    Live HLS entries sometimes carry no CODECS attribute, so yt-dlp reports
    vcodec/acodec 'unknown' - they then land in the 'progressive' bucket even
    though they are audio-only (no height, m3u8 protocol)."""
    out = []
    for f in (info.get("formats") or []):
        protocol = str(f.get("protocol") or "")
        try:
            height = int(f.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        vcodec = str(f.get("vcodec") or "").lower()
        if protocol.startswith("m3u8") and not height and \
                vcodec in ("none", "unknown", ""):
            out.append(f)
    return out


def _fmt_score(fmt, cap, codec_pref="Auto"):
    """Higher is better. Codec preference per the v3.2 selector."""
    try:
        height = int(fmt.get("height") or 0)
    except (TypeError, ValueError):
        height = 0
    try:
        tbr = float(fmt.get("tbr") or 0)
    except (TypeError, ValueError):
        tbr = 0.0
    try:
        fps = float(fmt.get("fps") or 0)
    except (TypeError, ValueError):
        fps = 0.0
    vcodec = fmt.get("vcodec") or ""
    score = 0.0
    if 0 < height <= cap:
        score += 1000 + height          # prefer as close to cap as possible
    elif height == 0:
        score += 100                    # unknown height, low priority
    else:
        score += max(0, 500 - (height - cap) * 2)   # above cap: penalise
    score += min(tbr, 2000) * 0.1
    score += min(fps, 60) * 0.5
    fam = codec_family(vcodec)
    if codec_pref == "Auto":
        if fam == "H.264":
            score += 800                # hardware decoder friendly
    elif fam == codec_pref:
        score += 2000                   # requested codec: decisive bonus
    return score


def _height_score(height, cap):
    """Height preference: closer to cap from below is better."""
    if height == 0:
        return 100
    if 0 < height <= cap:
        return 1000 + height
    return max(0, 500 - (height - cap) * 2)


def _lang_matches(fmt_lang, want):
    """Does an audio format's language code satisfy the wanted language?

    'pt' matches 'pt', 'pt-BR', 'pt-PT'; 'en' matches 'en', 'en-US'.
    A 'pt-BR' want also matches plain 'pt' (base code)."""
    if not want or not fmt_lang:
        return False
    fl = str(fmt_lang).split("-")[0].split(".")[0].lower()
    wl = str(want).split("-")[0].split(".")[0].lower()
    return fl == wl


def _fmt_is_original_audio(f):
    """yt-dlp marks the original audio track (see _video.py):
    language_preference == 10, or '(original)' inside format_note."""
    try:
        if int(f.get("language_preference") or 0) == 10:
            return True
    except (TypeError, ValueError):
        pass
    return "original" in str(f.get("format_note") or "").lower()


def _audio_lang_rank(f, want, orig_code):
    """Sort key: how well this audio format matches the wanted language.

    want == "Original" (default): original track first, then the video's
    original language code, then the default track, then the rest.
    want == 'pt'/'en'/...: matching language first (original variant of it
    above dubbed variants), then original track, then the rest."""
    is_orig = _fmt_is_original_audio(f)
    flang = f.get("language") or ""
    mp4a = 1 if (f.get("acodec") or "").startswith("mp4a") else 0
    try:
        tbr = float(f.get("tbr") or 0)
    except (TypeError, ValueError):
        tbr = 0.0
    if want and want != "Original":
        if _lang_matches(flang, want):
            return (3 + (1 if is_orig else 0), mp4a, tbr)
        if is_orig:
            return (2, mp4a, tbr)
        return (0, mp4a, tbr)
    # "Original"
    if is_orig:
        return (4, mp4a, tbr)
    if orig_code and _lang_matches(flang, orig_code):
        return (3, mp4a, tbr)
    try:
        if int(f.get("language_preference") or 0) == 5:   # default track
            return (2, mp4a, tbr)
    except (TypeError, ValueError):
        pass
    return (0, mp4a, tbr)


def available_audio_languages(info):
    """Audio languages present in a yt-dlp info dict.

    Returns a list of (code, label) with the original language FIRST
    (code 'original' + its real code as label), then every other distinct
    language code sorted. Empty list = single-track / no language data
    (the vast majority of videos)."""
    formats = info.get("formats") or []
    orig_code = None
    codes = []
    for f in formats:
        if (f.get("vcodec") or "none") != "none":
            continue
        ac = f.get("acodec") or "none"
        if ac == "none" and not f.get("_hls_audio_media"):
            continue
        lang = f.get("language") or ""
        if _fmt_is_original_audio(f):
            if lang:
                orig_code = lang
                if not any(c == "original" for c, _ in codes):
                    codes.append(("original", lang))
                continue
        if lang and not any(c == str(lang).lower() for c, _ in codes):
            codes.append((str(lang).lower(), lang))
    out = []
    if orig_code:
        out.append(("original", orig_code))
    else:
        for c, lab in codes:
            if c == "original":
                out.append((c, lab))
                break
    for c, lab in codes:
        if c != "original":
            out.append((c, lab))
    return out


def select_formats(info, quality="Auto", codec="Auto", ffmpeg_path=None,
                   audio_lang="Original"):
    """Pick best video (+ optional audio) formats from a yt-dlp info dict.

    Strategy: compare the best progressive stream with the best DASH
    (video-only + audio-only) pair and use whichever serves a height closer
    to the quality cap. Codec preference (v3.2): 'Auto' keeps the H.264 bias
    for hardware decoding; an explicit codec (H.264/VP9/AV1) restricts the
    candidates to that family when any exist, falling back gracefully.

    v3.6 audio language: multi-audio-track videos (YouTube auto-dubs) carry
    several audio formats tagged with a `language` code; the original track
    is the one yt-dlp marks language_preference 10 / '(original)'. The
    audio_lang preference ('Original' by default, or a code like 'pt')
    decides which track wins - before v3.6 the pick was pure bitrate, which
    regularly landed on a DUBBED track ("the audio is translating").

    v3.4 live handling: the audio playlist URL is read from the stream's
    MASTER manifest (EXT-X-MEDIA:TYPE=AUDIO group URI - the same thing
    SmartTube/mpv use), probed once and cached on the info dict. When the
    master has no audio group, a MUXED variant is returned instead and
    info['_pilastube_muxed_video'] is set so the player maps 0:a:0.

    Returns (video_url, audio_url_or_None, picked_height, format_note).
    Falls back to (None, None, 0, reason) when nothing usable was found.
    """
    cap = quality_cap(quality)
    if codec not in CODEC_OPTIONS:
        codec = "Auto"
    is_live = bool(info.get("is_live")) or \
        info.get("live_status") == "is_live"
    # v3.4: reset the muxed marker up front - THIS call's result decides it
    # (a codec switch can move from a muxed selection back to a paired one)
    if is_live:
        info["_pilastube_muxed_video"] = False
    formats = info.get("formats") or []
    usable = []
    for f in formats:
        url = f.get("url")
        if not url:
            continue
        vcodec = (f.get("vcodec") or "none")
        acodec = (f.get("acodec") or "none")
        if vcodec == "none" and acodec == "none":
            # v3.4 ROOT-CAUSE FIX (device log, SBT Ao Vivo): yt-dlp builds
            # its EXT-X-MEDIA audio renditions (the LIVE stream's audio
            # playlists!) with vcodec 'none' and NO acodec field at all
            # (see yt_dlp common.py extract_media). The drop below threw
            # them away, which is exactly why live video played silent
            # while VOD (DASH itag 140 audio, real acodec) worked.
            proto = str(f.get("protocol") or "")
            try:
                _hgt = int(f.get("height") or 0)
            except (TypeError, ValueError):
                _hgt = 0
            if proto.startswith("m3u8") and not _hgt:
                f = dict(f)
                f["acodec"] = "mp4a.40.2"   # HLS audio renditions are AAC
                f["_hls_audio_media"] = True
                usable.append(f)
            continue
        ext = (f.get("ext") or "").lower()
        if ext in ("mhtml", "sb0", "sb1", "sb2"):
            continue
        usable.append(f)

    def _h(f):
        try:
            return int(f.get("height") or 0)
        except (TypeError, ValueError):
            return 0

    progressive = [f for f in usable
                   if (f.get("vcodec") or "none") != "none"
                   and (f.get("acodec") or "none") != "none"]
    video_only = [f for f in usable
                  if (f.get("vcodec") or "none") != "none"
                  and (f.get("acodec") or "none") == "none"]
    audio_only = [f for f in usable
                  if (f.get("vcodec") or "none") == "none"
                  and (f.get("acodec") or "none") != "none"]
    if is_live:
        # v3.3: audio-only HLS entries with unknown codecs would otherwise
        # pollute the 'progressive' bucket; move them to the audio list
        hints = _live_hls_audio_hint({"formats": usable})
        known_urls = set(f.get("url") for f in audio_only)
        for f in hints:
            if f.get("url") not in known_urls:
                audio_only.append(f)
                if f in progressive:
                    progressive.remove(f)

    # v3.2: explicit codec restriction (with graceful fallback)
    if codec != "Auto":
        prog_c = [f for f in progressive
                  if codec_family(f.get("vcodec")) == codec]
        vid_c = [f for f in video_only
                 if codec_family(f.get("vcodec")) == codec]
        if prog_c or vid_c:
            progressive, video_only = prog_c, vid_c

    best_prog = max(progressive, key=lambda f: _fmt_score(f, cap, codec)) \
        if progressive else None
    best_v = max(video_only, key=lambda f: _fmt_score(f, cap, codec)) \
        if video_only else None
    best_a = None
    if audio_only:
        # v3.6: language-aware audio track pick. Multi-audio videos (auto
        # dubs) must respect the audio_lang preference - ORIGINAL by
        # default so YouTube's auto-dubbing never "translates" the audio
        # behind the user's back. Falls back to the old mp4a+bitrank order
        # when the formats carry no language info at all.
        orig_code = None
        for f in audio_only:
            if _fmt_is_original_audio(f) and f.get("language"):
                orig_code = f.get("language")
                break
        has_lang = any(f.get("language") for f in audio_only)
        if has_lang and audio_lang:
            best_a = max(audio_only, key=lambda f: _audio_lang_rank(
                f, audio_lang, orig_code))
            try:
                LOG("audio track: lang=%s note=%s (want %s)" % (
                    best_a.get("language"), (best_a.get("format_note") or
                                             "")[:24], audio_lang), "PLAYER")
            except Exception:
                pass
        else:
            # prefer AAC/mp4a (hardware friendly), then bitrate
            best_a = max(audio_only, key=lambda f: (
                1 if (f.get("acodec") or "").startswith("mp4a") else 0,
                float(f.get("tbr") or 0)))

    # v3.4: live audio pairing. When the fixed usable-filter above already
    # surfaced yt-dlp's EXT-X-MEDIA audio rendition (vcodec 'none', no
    # acodec - the classic live audio entry), we are done: best_a holds the
    # REAL audio playlist URL. Otherwise the MASTER manifest is fetched and
    # its EXT-X-MEDIA:TYPE=AUDIO group URI is used (this is what SmartTube
    # and mpv do). Probed once and cached on the info dict so quality/codec
    # switches never hit the network again.
    live_audio_url = None
    if is_live and best_a is None:
        master_url = None
        for f in usable:
            mu = str(f.get("manifest_url") or "")
            if mu and ("manifest" in mu or ".m3u8" in mu):
                master_url = mu
                break
        if not master_url:
            iu = str(info.get("url") or "")
            if "manifest" in iu or ".m3u8" in iu:
                master_url = iu
        info["_pilastube_live_master"] = master_url
        live_audio_url = info.get("_pilastube_live_master_audio")
        # v3.6: re-pick when the audio language changed since the cached
        # pick (False = "probed, no audio group at all" stays cached).
        cached_lang = info.get("_pilastube_live_master_audio_lang")
        if live_audio_url and cached_lang is not None \
                and cached_lang != (audio_lang or "Original"):
            live_audio_url = None
        if live_audio_url is None:
            # not probed yet (or language changed): fetch + parse the
            # master now, then (only if it had no audio group) fall back
            # to the ffmpeg-verified itag swap
            try:
                live_audio_url, variants = live_audio_from_master(
                    master_url, timeout=6, want_lang=audio_lang)
                # v3.6: expose the live renditions' languages to the
                # in-player audio menu (same shape as the VOD path)
                rends = live_master_audio_langs()
                if rends:
                    info["_pilastube_audio_langs"] = [
                        (code if code else "original", name)
                        for code, name, _uri in rends]
            except Exception:
                live_audio_url, variants = None, []
            if not live_audio_url and ffmpeg_path and best_v is not None:
                try:
                    live_audio_url, muxed_try = probe_live_audio(
                        best_v.get("url"), master_url, ffmpeg_path)
                    if muxed_try:
                        info["_pilastube_live_muxed"] = muxed_try
                except Exception:
                    pass
            info["_pilastube_live_master_audio"] = live_audio_url or False
            info["_pilastube_live_master_audio_lang"] = audio_lang or \
                "Original"
            info.setdefault("_pilastube_live_variants", variants or [])
            if live_audio_url:
                LOG_LIVE_AUDIO(live_audio_url)
    if is_live and best_a is None and live_audio_url:
        best_a = {"url": live_audio_url, "acodec": "mp4a.40.2",
                  "tbr": 128, "_derived": True}

    # v3.4: no audio track at all -> a MUXED variant still carries sound.
    # Return the muxed URL as the video input and mark it so the player maps
    # 0:a:0 (audio inside the video input) instead of a second input.
    if is_live and best_a is None and best_v is not None:
        muxed_url = info.get("_pilastube_live_muxed")
        if muxed_url is None:
            muxed_url = False
            variants = info.get("_pilastube_live_variants") or []
            best_mh = None
            for v in variants:
                if not _variant_is_muxed(v.get("attrs") or {}):
                    continue
                try:
                    vh = int((v.get("attrs") or {}).get("RESOLUTION", "x")
                             .split("x")[-1] or 0)
                except (TypeError, ValueError):
                    vh = 0
                if best_mh is None or (0 < vh <= cap and
                                       abs(vh - cap) < abs(best_mh - cap)):
                    best_mh, muxed_url = vh, v.get("url")
            info["_pilastube_live_muxed"] = muxed_url
        if muxed_url:
            try:
                print("[PLAYER] live: no audio group - "
                      "using muxed variant (single input)")
            except Exception:
                pass
            info["_pilastube_muxed_video"] = True
            _mh = _h(best_v)
            return muxed_url, None, _mh, "%dp muxed" % _mh

    # decide between progressive and DASH pair based on height fit
    use_dash = False
    if best_v is not None and best_a is not None:
        if best_prog is None:
            use_dash = True
        else:
            ph = _height_score(_h(best_prog), cap)
            dh = _height_score(_h(best_v), cap)
            if dh > ph + 100:      # DASH is meaningfully closer to the cap
                use_dash = True

    def _note(f):
        fam = codec_family(f.get("vcodec"))
        base = "%sp%s" % (f.get("height") or "?",
                           int(float(f.get("fps") or 0)))
        if codec != "Auto" and fam:
            return "%s %s" % (fam, base)
        return base

    if use_dash:
        return best_v["url"], best_a["url"], best_v.get("height") or 0, \
            _note(best_v)
    if best_prog is not None:
        return best_prog["url"], None, best_prog.get("height") or 0, \
            _note(best_prog)
    if best_v is not None:   # video without any audio track available
        return best_v["url"], None, best_v.get("height") or 0, _note(best_v)
    if usable:
        best = max(usable, key=lambda f: _fmt_score(f, cap, codec))
        return best["url"], None, best.get("height") or 0, "fallback"

    return None, None, 0, "no-formats"


# ----------------------------------------------------------------------------
# Chapters / subtitles helpers for mpv
# ----------------------------------------------------------------------------
def build_chapters_xml(chapters, path):
    """Write mpv-compatible Matroska XML chapters file. Returns True on success."""
    if not chapters:
        return False
    try:
        parts = ['<?xml version="1.0"?>', '<!DOCTYPE Chapters SYSTEM "matroskachapters.dtd">',
                 "<Chapters>"]
        for ch in chapters:
            start = ch.get("start_time", 0) or 0
            try:
                start = float(start)
            except (TypeError, ValueError):
                start = 0.0
            h = int(start // 3600)
            m = int((start % 3600) // 60)
            s = start % 60
            stamp = "%02d:%02d:%06.3f" % (h, m, s)
            title = (ch.get("title") or "Chapter").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            parts.append("  <ChapterAtom>")
            parts.append("    <ChapterTimeStart>%s</ChapterTimeStart>" % stamp)
            parts.append("    <ChapterDisplay>")
            parts.append("      <ChapterString>%s</ChapterString>" % title)
            parts.append("    </ChapterDisplay>")
            parts.append("  </ChapterAtom>")
        parts.append("</Chapters>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        return True
    except Exception as e:
        print("chapters write error: %s" % e)
        return False


SUB_LANG_MAP = {"Auto": None, "Off": False, "English": "en", "Portugues": "pt",
                "Espanol": "es", "Turkce": "tr", "Deutsch": "de", "Francais": "fr"}

# v3.6 audio track language selection (multi-audio / auto-dubbed videos).
# "Original" = the track yt-dlp marks as the video's original audio
# (language_preference 10 / '(original)' in format_note); the rest pick a
# dubbed track by language code. Used by the Settings row and the
# in-player Audio menu; maps UI labels -> ISO codes.
AUDIO_LANG_OPTIONS = ["Original", "Portugues", "English", "Espanol",
                      "Deutsch", "Francais", "Italiano", "Japones"]
AUDIO_LANG_MAP = {
    "Original": "Original",
    "Portugues": "pt", "English": "en", "Espanol": "es",
    "Deutsch": "de", "Francais": "fr", "Italiano": "it", "Japones": "ja",
}

# v3.6: yt-dlp metadata language - makes YouTube serve search / feed
# metadata in the user's language instead of auto-translating everything
# to English (the "titles are always in english" complaint + the garbage
# "Minecraft1895"-style titles both come from requesting with hl=en -
# verified 2026-08-31: default search returns "Friday0828" and English
# titles, lang=pt returns the real Portuguese ones).
YTDLP_LANG = {
    "English": "en", "Portugues": "pt", "Espanol": "es",
    "Turkce": "tr", "Deutsch": "de", "Francais": "fr",
}


def pick_subtitle(info, pref, download_path, prefs):
    """Choose + download a subtitle track. Returns file path or None."""
    lang = None
    if pref == "Off":
        return None
    if pref in SUB_LANG_MAP and isinstance(SUB_LANG_MAP[pref], str):
        lang = SUB_LANG_MAP[pref]
    else:
        # Auto: UI language handled by caller passing pref like 'pt'
        lang = pref if (len(pref) == 2 and pref.isalpha()) else None

    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    chosen_url = None
    # manual subs in preferred language first
    for source, table in (("manual", subs), ("auto", autos)):
        if not lang:
            continue
        for track in table.get(lang, []):
            ext = (track.get("ext") or "").lower()
            if ext in ("vtt", "srv1", "srv2", "srv3", "json3"):
                if ext == "vtt" or not chosen_url:
                    chosen_url = track.get("url")
                if ext == "vtt":
                    break
        if chosen_url:
            break
    # fallback: first available manual track of any language
    if not chosen_url:
        for tracks in subs.values():
            for track in tracks:
                if (track.get("ext") or "").lower() == "vtt":
                    chosen_url = track.get("url")
                    break
            if chosen_url:
                break
    if not chosen_url:
        return None
    try:
        raw = net_get(chosen_url, timeout=10, proxy=prefs.get("proxy", ""),
                      prefer_ipv4=prefs.get("prefer_ipv4", "Off") == "On")
        with open(download_path, "wb") as f:
            f.write(raw)
        return download_path
    except Exception as e:
        print("subtitle download fail: %s" % e)
        return None


# ----------------------------------------------------------------------------
# yt-dlp self-update
# ----------------------------------------------------------------------------
def ytdlp_version(ytdlp_path):
    if not ytdlp_path or not os.path.exists(ytdlp_path):
        return "not found"
    try:
        r = subprocess.run([ytdlp_path, "--version"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=25)
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def ytdlp_latest_version(proxy=""):
    try:
        api = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
        hdrs = {"Accept": "application/vnd.github+json"}
        raw = net_get(api, timeout=15, proxy=proxy, headers=hdrs)
        data = json.loads(raw.decode("utf-8", "replace"))
        return (data.get("tag_name") or "").strip()
    except Exception:
        return ""


def ytdlp_self_update(ytdlp_path, proxy="", progress_cb=None):
    """Download the latest standalone aarch64 yt-dlp and replace the binary.

    Returns (ok, message).
    """
    def cb(msg):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    cb("Checking latest version...")
    latest = ytdlp_latest_version(proxy)
    if not latest:
        return False, "Could not reach GitHub"
    current = ytdlp_version(ytdlp_path)
    if latest == current:
        return True, "Already up to date (%s)" % current
    cb("Downloading yt-dlp %s..." % latest)
    url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux_aarch64"
    try:
        raw = net_get(url, timeout=300, proxy=proxy)
        if len(raw) < 1000000:
            return False, "Download too small - aborted"
        tmp = ytdlp_path + ".new"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.chmod(tmp, 0o755)
        # try clean replace; fall back to in-place move
        try:
            os.replace(tmp, ytdlp_path)
        except OSError:
            bak = ytdlp_path + ".bak"
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(ytdlp_path, bak)
            os.replace(tmp, ytdlp_path)
        os.chmod(ytdlp_path, 0o755)
        return True, "Updated to %s (was %s)" % (latest, current)
    except Exception as e:
        return False, "Update failed: %s" % e


# ----------------------------------------------------------------------------
# Content filters (hide shorts / live / watched)
# ----------------------------------------------------------------------------
def _vid_get(video, key, default=None):
    """Read a field from either a dict or a VideoItem-like object."""
    if isinstance(video, dict):
        return video.get(key, default)
    return getattr(video, key, default)


def is_short(video):
    d = _vid_get(video, "duration")
    if not d:
        return False
    try:
        d = float(d)
    except (TypeError, ValueError):
        return False
    if d <= 0:
        return False
    title = (_vid_get(video, "title") or "").lower()
    return d <= 61 or "#shorts" in title or " #short" in title


def is_live_item(video):
    if _vid_get(video, "is_live"):
        return True
    if _vid_get(video, "live_status") in ("is_live", "post_live", "is_upcoming"):
        return True
    d = _vid_get(video, "duration")
    if _vid_get(video, "source", "ytdlp") == "ytdlp" and not d:
        return True
    return False


def apply_filters(items, prefs, positions, history_ids=None):
    """Filter a list of video dicts by the user's content preferences."""
    hide_shorts = prefs.get("hide_shorts", "Off") == "On"
    hide_live = prefs.get("hide_live", "Off") == "On"
    hide_watched = prefs.get("hide_watched", "Off") == "On"
    if not (hide_shorts or hide_live or hide_watched):
        return items
    out = []
    for v in items:
        if hide_shorts and is_short(v):
            continue
        if hide_live and is_live_item(v):
            continue
        if hide_watched:
            vid = _vid_get(v, "id")
            pos = positions.get(vid)
            d = _vid_get(v, "duration")
            if pos and d:
                p, dur = pos[0], pos[1] if isinstance(pos, (list, tuple)) else (pos, d)
                try:
                    if dur > 0 and (p / float(dur)) > 0.9:
                        continue
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        out.append(v)
    return out


# ----------------------------------------------------------------------------
# Trending feed (v2.2)
#
# YouTube removed the classic /feed/trending page for API clients in 2025
# (the URL now redirects to the home page, so yt-dlp returns nothing).
# PilasTube therefore builds the Trending tab from a fallback chain:
#   1. Piped public API     (per-region trending, no key)
#   2. Invidious public API (per-region trending, no key)
#   3. yt-dlp charts playlist (Top 100 music videos)
#   4. yt-dlp search mix    (handled by the app)
#   5. local disk cache of the last successful feed
# ----------------------------------------------------------------------------
PIPED_INSTANCES = [
    "https://api.piped.private.coffee",
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.reallyaweso.me",
]

INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://iv.melmac.space",
    "https://yewtu.be",
]

# Trending region derived from the interface language (no extra setting)
TREND_REGIONS = {
    "Portugues": "BR",
    "English": "US",
    "Espanol": "MX",
    "Deutsch": "DE",
    "Francais": "FR",
    "Turkce": "TR",
}

# Official YouTube "Top 100 Music Videos Global" charts playlist - this one
# still works with yt-dlp --flat-playlist (unlike /feed/trending).
CHARTS_PLAYLIST_ID = "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"

# Disk cache of the last successful home feed (offline / "api down" rescue)
HOME_CACHE_FILE = os.path.join(SCRIPT_DIR, "yt_home_cache.json")
HOME_CACHE_MAX = 60


def trend_region_for_language(language):
    """Map the interface language to a trending region code."""
    return TREND_REGIONS.get(language or "", "US")


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _piped_entry_to_video(entry):
    """Normalise one Piped /trending item, or None if unusable."""
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", entry.get("url", "") or "")
    if not m:
        return None
    dur = _safe_int(entry.get("duration"), -1)
    is_live = dur < 0
    cid = re.search(r"/channel/([A-Za-z0-9_-]+)",
                    entry.get("uploaderUrl", "") or "")
    return {
        "id": m.group(1),
        "title": entry.get("title") or "Unknown",
        "channel": entry.get("uploaderName") or "Unknown",
        "channel_id": cid.group(1) if cid else "",
        "duration": None if is_live else dur,
        "view_count": _safe_int(entry.get("views"), 0),
        "is_live": is_live,
        "live_status": "is_live" if is_live else "",
        "source": "piped",
    }


def _invidious_entry_to_video(entry):
    """Normalise one Invidious /api/v1/trending item, or None if unusable."""
    vid = entry.get("videoId") or ""
    if not vid:
        return None
    is_live = bool(entry.get("liveNow"))
    dur = _safe_int(entry.get("lengthSeconds"), 0)
    return {
        "id": vid,
        "title": entry.get("title") or "Unknown",
        "channel": entry.get("author") or "Unknown",
        "channel_id": entry.get("authorId") or "",
        "duration": None if is_live else dur,
        "view_count": _safe_int(entry.get("viewCount"), 0),
        "is_live": is_live,
        "live_status": "is_live" if is_live else "",
        "source": "invidious",
    }


def fetch_api_trending(region="US", proxy="", prefer_ipv4=False,
                       timeout=8, max_items=30):
    """Try Piped then Invidious public trending endpoints.

    Returns (videos, source_label). videos is [] when every instance failed;
    source_label describes the last attempt for the log.
    """
    label = "none"
    for base in PIPED_INSTANCES:
        url = "%s/trending?region=%s" % (base, region)
        label = "piped:%s" % base.split("//")[-1]
        try:
            data = net_get_json(url, timeout=timeout, proxy=proxy,
                                prefer_ipv4=prefer_ipv4)
        except Exception as e:
            continue
        if not isinstance(data, list):
            continue
        videos = [v for v in
                  (_piped_entry_to_video(e) for e in data if e)
                  if v]
        if videos:
            return videos[:max_items], label
    for base in INVIDIOUS_INSTANCES:
        url = "%s/api/v1/trending?region=%s" % (base, region)
        label = "invidious:%s" % base.split("//")[-1]
        try:
            data = net_get_json(url, timeout=timeout, proxy=proxy,
                                prefer_ipv4=prefer_ipv4)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        videos = [v for v in
                  (_invidious_entry_to_video(e) for e in data if e)
                  if v]
        if videos:
            return videos[:max_items], label
    return [], label


def charts_playlist_url():
    """yt-dlp-usable URL of the Top 100 music charts playlist."""
    return "https://www.youtube.com/playlist?list=%s" % CHARTS_PLAYLIST_ID


def save_home_cache(video_dicts, source=""):
    """Persist the last good home feed for offline reuse."""
    try:
        payload = {
            "ts": int(time.time()),
            "source": source,
            "videos": list(video_dicts)[:HOME_CACHE_MAX],
        }
        with open(HOME_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return True
    except Exception:
        return False


def load_home_cache(max_age_hours=168):
    """Return (video_dicts, saved_epoch) from the cache, or ([], 0)."""
    try:
        with open(HOME_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        videos = payload.get("videos") or []
        ts = _safe_int(payload.get("ts"), 0)
        if not videos:
            return [], 0
        if max_age_hours and ts and \
                time.time() - ts > max_age_hours * 3600:
            return [], 0          # stale beyond one week
        return videos, ts
    except Exception:
        return [], 0


# ----------------------------------------------------------------------------
# v3.4: CATEGORY BROWSING (SmartTube-style "Explore" sections)
#
# /feed/trending now redirects to the YouTube home page for the plain web
# client, so category feeds are built from what provably works on the
# device TODAY:
#   * Trending  -> the Piped/Invidious public trending APIs (region-aware)
#   * Music     -> the official Top-100 charts playlist (yt-dlp flat list)
#   * others    -> YouTube SEARCH with server-side filter tokens (sp=):
#       - sp=EgJAAQ%3D%3D  = LIVE filter   (real live streams only)
#       - sp=CAMSAhAB      = videos sorted by VIEW COUNT ("most viewed")
#     yt-dlp extracts these URLs exactly like a normal search, so the whole
#     existing flat-playlist pipeline is reused. Queries are per-language
#     (EN + PT first, matching the app's translation coverage).
# ----------------------------------------------------------------------------
SP_LIVE = "EgJAAQ%3D%3D"
SP_MOST_VIEWED = "CAMSAhAB"


def search_filter_url(query, sp=None):
    """A YouTube results URL with a server-side filter token (yt-dlp-ready)."""
    q = urllib.parse.quote(query or "")
    if sp:
        return ("https://www.youtube.com/results?search_query=%s&sp=%s"
                % (q, sp))
    return "https://www.youtube.com/results?search_query=%s" % q


# Per-language search queries per category key. Languages not listed fall
# back to English (same policy as the translation tables).
CATEGORY_QUERIES = {
    "gaming": {"English": "gaming", "Portugues": "jogos"},
    "music": {"English": "music", "Portugues": "musica"},
    "live": {"English": "live news", "Portugues": "ao vivo"},
    "movies": {"English": "full movies", "Portugues": "filmes completos"},
    "news": {"English": "news live", "Portugues": "noticias ao vivo"},
    "sports": {"English": "sports highlights", "Portugues": "melhores momentos esportes"},
    "learning": {"English": "documentary", "Portugues": "documentario"},
    "podcasts": {"English": "podcast", "Portugues": "podcast"},
}

# Categories that use the LIVE search filter instead of most-viewed.
CATEGORY_LIVE_FILTER = ("live", "news")


def category_query(key, language):
    """Search query for a category in the user's language."""
    pack = CATEGORY_QUERIES.get(key) or {}
    return pack.get(language) or pack.get("English") or key


def category_search_url(key, language):
    """yt-dlp URL for a category's video feed (filter token included)."""
    return search_filter_url(category_query(key, language),
                             SP_LIVE if key in CATEGORY_LIVE_FILTER
                             else SP_MOST_VIEWED)
