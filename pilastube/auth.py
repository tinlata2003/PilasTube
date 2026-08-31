#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PilasTube v3.5 - auth.py (account domain)

AuthMixin: the SmartTube-style sign-in - the yt.be/activate device-code
flow (start_login -> _login_worker poll), token persistence and refresh
(_restore_account), identity fetch, channel import, the account's own
subscription feed and watch history, sign-out and the full-screen login
renderer. Token storage / InnerTube OAuth live in yt_extras.YouTubeAuth.
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
    LOG,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    SLOG,
    VideoItem,
    run_logged_thread,
)
import yt_extras as YX

from yt_extras import YouTubeAuth  # noqa: F401  (re-export: auth home)

class AuthMixin(object):
    """Account login, restore, feed and history.
    Hosted by PilasTubeApp; all state lives on self."""
    def _load_account_subs(self, force=False):
        """The signed-in account's own subscription feed (SmartTube path:
        innertube browse FEsubscriptions with the TV Bearer token)."""
        if not force and self.sub_videos and \
                (time.time() - self._account_subs_at) < 600:
            return
        self.subs_loading = True
        self.set_status(self.t("msg_loading_videos"), "loading")
        self.need_redraw = True
        self.render()

        def worker():
            try:
                hl = YX.YTDLP_LANG.get(self.prefs.get("language", "English"), "en")
                gl = YX.TREND_REGIONS.get(self.prefs.get("language", "English"), "US")
                items = YX.get_auth().fetch_account_feed("subscriptions",
                                                         hl=hl, gl=gl)
                if items:
                    self.sub_videos = [VideoItem(v) for v in items]
                    self._account_subs_at = time.time()
                    self.account_feed_fallback = False
                    self.subs_loading = False
                    self.set_status("%d %s - %s" % (
                        len(self.sub_videos), self.t("msg_videos"),
                        self.t("subs_account")))
                    LOG("[AUTH] account subs feed: %d items" % len(items),
                        "AUTH")
                    self.need_redraw = True
                    return
                raise RuntimeError("empty feed")
            except Exception as e:
                LOG("[AUTH] account subs feed failed -> RSS fallback: %s" % e,
                    "AUTH")
            self.account_feed_fallback = True
            self.subs_loading = False
            self.set_status(self.t("msg_account_feed_fail"))
            self.need_redraw = True
            # local RSS refresh as the fallback (own thread; no main-thread
            # render from inside this worker)
            self._load_rss_subs(force=True, render_first=False)

        run_logged_thread("account-subs", worker)

    def _account_ready(self):
        try:
            return YX.get_auth().logged_in()
        except Exception:
            return False

    def _maybe_load_account_history(self):
        """History tab: fetch the account's YouTube watch history (at most
        every 10 minutes; falls back to the local history silently)."""
        if not self._account_ready() or self.account_history_loading:
            return
        if self.account_history and \
                (time.time() - self.account_history_at) < 600:
            return
        self.account_history_loading = True
        self.need_redraw = True

        def worker():
            try:
                hl = YX.YTDLP_LANG.get(self.prefs.get("language", "English"), "en")
                gl = YX.TREND_REGIONS.get(self.prefs.get("language", "English"), "US")
                items = YX.get_auth().fetch_account_feed("history",
                                                         hl=hl, gl=gl)
                if items:
                    self.account_history = [VideoItem(v) for v in items]
                    LOG("[AUTH] account watch history: %d items" % len(items),
                        "AUTH")
                else:
                    LOG("[AUTH] account watch history empty - local fallback",
                        "AUTH")
            except Exception as e:
                LOG("[AUTH] watch history failed - local fallback: %s" % e,
                    "AUTH")
            self.account_history_at = time.time()
            self.account_history_loading = False
            self.need_redraw = True

        run_logged_thread("account-history", worker, daemon=True)

    # =====================================================================
    # v3.3 ACCOUNT LOGIN (SmartTube TV device-code flow)
    # =====================================================================
    def start_login(self):
        """Settings > Account > Sign in: show a code the user enters at
        yt.be/activate on another device (SmartTube's exact flow)."""
        if self.login_active:
            return
        auth = YX.get_auth()
        if auth.logged_in():
            name = auth.account_name() or "YouTube"
            self.set_status("%s %s" % (self.t("settings_signed_in_as"),
                                       name[:20]))
            return
        # v3.3: session tag - a cancelled/superseded worker from an older
        # attempt must never clear the state of a newer one (it can wake
        # from a poll sleep long after cancel_login returned)
        self._login_session = getattr(self, "_login_session", 0) + 1
        session = self._login_session
        self.login_active = True
        self.login_state = {"phase": "request", "code": "", "url":
                            YX.SIGNIN_URL, "alt_url": "", "error": "",
                            "account": ""}
        self.login_cancel = threading.Event()
        self.need_redraw = True
        SLOG("Sign-in started (device code)")
        LOG("[AUTH] device-code login started", "AUTH")
        run_logged_thread("login", self._login_worker, args=(session,),
                          daemon=True)

    def _login_worker(self, session):
        """Background: request the code, poll for the token, fetch identity."""
        auth = YX.get_auth()
        cancel = self.login_cancel
        state = self.login_state

        def _stale():
            return session != getattr(self, "_login_session", session)

        def _cancelled():
            return _stale() or (cancel is not None and cancel.is_set())

        try:
            try:
                code = auth.request_device_code()
            except YX.OAuthError as e:
                if _stale():
                    return
                state["phase"] = "failed"
                state["error"] = self._login_error_text(e)
                LOG("[AUTH] device code failed: %s" % e, "AUTH")
                self.need_redraw = True
                return
            state["code"] = str(code.get("user_code") or "")
            state["url"] = YX.SIGNIN_URL
            alt = str(code.get("verification_url") or "")
            state["alt_url"] = alt if "google.com/device" in alt else \
                "https://www.google.com/device"
            try:
                interval = max(2, int(code.get("interval") or 5))
                expires_in = int(code.get("expires_in") or 1800)
            except (TypeError, ValueError):
                interval, expires_in = 5, 1800
            state["phase"] = "code"
            self.need_redraw = True
            LOG("[AUTH] code %s - enter at %s (expires in %ss)" %
                (state["code"], YX.SIGNIN_URL, expires_in), "AUTH")
            auth.poll_token(code.get("device_code"), interval=interval,
                            expires_at=time.time() + expires_in,
                            cancelled=_cancelled)
            # token acquired - identity is best-effort (may 401 on some
            # accounts; the session still works for feeds)
            name = ""
            try:
                accs = auth.accounts_list()
                if accs:
                    name = str(accs[0].get("name") or "")
            except Exception as e:
                LOG("[AUTH] accounts_list failed (ignored): %s" % e, "AUTH")
            self.account_name = name or auth.account_name() or ""
            self.account_avatar = auth.account_avatar()
            if _stale():
                # a newer login attempt superseded this one - drop quietly
                return
            self.login_active = False
            self.login_state = {}
            self._update_settings_items()
            msg = self.t("login_success")
            if self.account_name and "%s" in msg:
                msg = msg % self.account_name
            elif not self.account_name:
                msg = msg.replace(" %s", "")
            self.set_status(msg)
            self.need_redraw = True
            LOG("[AUTH] signed in: %s" % (self.account_name or "(no name)"),
                "AUTH")
            SLOG("Signed in: %s" % (self.account_name or "account"))
            # v0.3.7: the account feeds are live NOW - drop the logged-out
            # home feed so the Recommended tab rebuilds with the account's
            # own recommendations (the main loop reloads it on the home tab)
            self._account_home_at = 0.0
            self._reload_home_after_login = True
            self.need_redraw = True
            # merge the account's channels into the local list (RSS fallback
            # + channel browsing now reflect the account too)
            self._import_account_channels()
        except YX.OAuthError as e:
            if _stale():
                return
            if e.kind == "cancelled":
                self.login_active = False
                self.login_state = {}
                self.need_redraw = True
                return
            state["phase"] = "failed"
            state["error"] = self._login_error_text(e)
            LOG("[AUTH] login failed: %s" % e, "AUTH")
            self.need_redraw = True
        except Exception as e:
            if _stale():
                return
            state["phase"] = "failed"
            state["error"] = str(e)[:60]
            LOG("[AUTH] login error: %s" % e, "AUTH")
            self.need_redraw = True

    def _login_error_text(self, err):
        kind = getattr(err, "kind", "")
        if kind == "denied":
            return self.t("login_denied")
        if kind in ("expired", "expired_token"):
            return self.t("login_expired")
        return "%s (%s)" % (self.t("login_failed"), str(err)[:32])

    def cancel_login(self):
        if self.login_cancel is not None:
            self.login_cancel.set()
        self.login_active = False
        self.login_state = {}
        self.need_redraw = True
        LOG("[AUTH] login cancelled by user", "AUTH")

    def sign_out_account(self):
        auth = YX.get_auth()
        auth.sign_out()
        self.account_name = ""
        self.account_avatar = ""
        self.account_history = []
        self.account_history_at = 0.0
        self.account_feed_fallback = False
        self._update_settings_items()
        self.set_status("%s: OK" % self.t("settings_logout"))
        self.need_redraw = True
        LOG("[AUTH] signed out", "AUTH")
        SLOG("Signed out")

    def _restore_account(self):
        """Startup: refresh the access token + identity so the session
        survives reboots (the 'stay there' part of SmartTube login).

        v0.3.7: the identity is also refreshed when the saved pageid is
        missing - the X-Goog-Pageid header (brand/channel identity) is what
        lets the authenticated browse calls return the account's real
        home/subscriptions/history instead of the anonymous shell."""
        auth = YX.get_auth()
        if not auth.logged_in():
            return
        try:
            auth.ensure_access_token()
            if not auth.account_name() or not auth.data.get("pageid"):
                try:
                    auth.accounts_list()
                except Exception as e:
                    LOG("[AUTH] identity refresh failed (ignored): %s" % e,
                        "AUTH")
            self.account_name = auth.account_name()
            self.account_avatar = auth.account_avatar()
            LOG("[AUTH] session restored: %s" % (self.account_name or "ok"),
                "AUTH")
            self._update_settings_items()
            self.need_redraw = True
            self._import_account_channels()
        except YX.OAuthError as e:
            LOG("[AUTH] session restore failed: %s" % e, "AUTH")
            if e.kind == "revoked":
                # refresh token revoked - drop the dead session quietly
                auth.sign_out()
                self._update_settings_items()
        except Exception as e:
            LOG("[AUTH] session restore error: %s" % e, "AUTH")

    def _import_account_channels(self):
        """Merge the account's subscribed channels into the local list so
        channel browsing + the RSS feed fallback match the account."""
        try:
            auth = YX.get_auth()
            if not auth.logged_in():
                return
            channels = auth.fetch_account_channels()
            if not channels:
                return
            known = set()
            for c in (self.subs.channels or []):
                known.add(c.get("channel_id"))
            added = 0
            for ch in channels[:200]:
                cid = ch.get("channel_id") or ""
                if cid and cid not in known:
                    self.subs.channels.append({
                        "channel_id": cid,
                        "name": ch.get("name") or cid,
                        "added": time.time()})
                    known.add(cid)
                    added += 1
            if added:
                self.subs.save_channels()
                LOG("[AUTH] imported %d account channels into local list" %
                    added, "AUTH")
        except Exception as e:
            LOG("[AUTH] channel import failed (ignored): %s" % e, "AUTH")

    def render_login_screen(self):
        """Full-screen SmartTube-style sign-in: code + yt.be/activate."""
        s = self.login_state or {}
        self.draw_rect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT, self.C.BG_PRIMARY)
        self.draw_logo(SCREEN_WIDTH // 2 - 22, 34, 44, 30)
        self.draw_text_centered(self.t("login_title"), SCREEN_WIDTH // 2, 84,
                                self.C.TEXT_PRIMARY, self.font_large)
        phase = s.get("phase")
        if phase == "code":
            self.draw_text_centered(self.t("login_open"),
                                    SCREEN_WIDTH // 2, 142,
                                    self.C.TEXT_SECONDARY, self.font_small)
            self.draw_text_centered("yt.be/activate", SCREEN_WIDTH // 2, 164,
                                    self.C.YT_RED, self.font)
            self.draw_text_centered(self.t("login_enter"),
                                    SCREEN_WIDTH // 2, 196,
                                    self.C.TEXT_SECONDARY, self.font_small)
            # the code itself, SmartTube-style (dashes as spaces)
            shown = str(s.get("code") or "...").replace("-", " ")
            cy = 226
            self.draw_rect(SCREEN_WIDTH // 2 - 175, cy, 350, 62,
                           self.C.CARD_BG)
            self.draw_rect(SCREEN_WIDTH // 2 - 175, cy, 350, 2, self.C.YT_RED)
            self.draw_text_centered(shown, SCREEN_WIDTH // 2, cy + 18,
                                    self.C.TEXT_PRIMARY, self.font_large)
            # waiting + spinner
            self.draw_spinner(SCREEN_WIDTH // 2, 328, 11)
            self.draw_text_centered(self.t("login_waiting"),
                                    SCREEN_WIDTH // 2, 352,
                                    self.C.TEXT_TERTIARY, self.font_small)
            self.draw_text_centered(self.t("login_cancel_hint"),
                                    SCREEN_WIDTH // 2, 442,
                                    self.C.TEXT_TERTIARY, self.font_tiny)
        elif phase in ("request", "done"):
            self.draw_spinner(SCREEN_WIDTH // 2, 210, 12)
            self.draw_text_centered(self.t("msg_loading"),
                                    SCREEN_WIDTH // 2, 238,
                                    self.C.TEXT_TERTIARY, self.font_small)
            self.draw_text_centered(self.t("login_cancel_hint"),
                                    SCREEN_WIDTH // 2, 442,
                                    self.C.TEXT_TERTIARY, self.font_tiny)
        else:   # failed
            self.draw_text_centered(self.t("login_failed"),
                                    SCREEN_WIDTH // 2, 200,
                                    self.C.YT_RED, self.font_large)
            err = str(s.get("error") or "")
            if err:
                self.draw_text_centered(err[:44], SCREEN_WIDTH // 2, 234,
                                        self.C.TEXT_SECONDARY, self.font_small)
            self.draw_text_centered(self.t("login_cancel_hint"),
                                    SCREEN_WIDTH // 2, 442,
                                    self.C.TEXT_TERTIARY, self.font_tiny)
