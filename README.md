<p align="center">
  <img src="https://i.ibb.co/KpgkcBNv/zezimmamaer.webp" alt="PilasTube" width="1086">
</p>

# PilasTube

**A native YouTube client for PortMaster handhelds** — inspired by
[SmartTube](https://github.com/yuliskov/SmartTube), designed for gamepads.

`v0.3.7` · R36S / R35S / RG35XX Plus/H / RGB30 and friends

| | |
|---|---|
| **Screen** | 640×480, dark UI built for small screens |
| **Input** | Gamepad only — no keyboard, no mouse, ever |
| **Languages** | English & Português (Türkçe, Español, Deutsch, Français too) |
| **Account** | Optional — sign in at **yt.be/activate** for your recommendations, subscriptions and history |
| **Player** | Built-in (ffmpeg): video + sound + on-screen HUD |
| **Ads** | None. Ever. |

## What is PilasTube?

PilasTube is a YouTube app that runs natively on PortMaster handheld
consoles. Everything — the menus, the keyboard, the video player — is
drawn by the app itself on a 640×480 screen and driven 100% by your
gamepad: browse, search, sign in, change settings and watch videos without
ever touching a keyboard or mouse.

It plays videos with its own built-in player (powered by your firmware's
`ffmpeg`): picture and sound together, with a SmartTube-style on-screen
HUD — progress bar, chapters, seeks, speed, quality switching, subtitles
and audio-track language selection.

You can use it without any account (streams are extracted directly, so
there are no ads), but signing in with your YouTube account unlocks your
personalized **Recommended** feed, your **Subscriptions** and your watch
**History** on the device — using the same device-code sign-in SmartTube
uses (you'll never type a password on the console).

## Highlights

- **Six sections**: Recommended · Subscriptions · Search · Categories ·
  History · Settings
- **Account sign-in** (yt.be/activate device code) — recommendations,
  subscriptions feed, watch history; the login persists across reboots
- **Categories**: Trending, Music, Gaming, Live, Movies, News, Sports,
  Learning, Podcasts + your Favorites
- **Built-in player**: pause/resume, ±10 s seeks, chapters, speed 0.5×–2×,
  quality switch without losing your position, subtitles, volume
- **Multi-audio support**: pick the audio language (Original by default —
  no surprise auto-dubs), in Settings and while playing
- **Search**: paginated results (16/page), on-demand suggestions (SUG key),
  recent searches, symbol page
- **SponsorBlock** auto-skip, **DeArrow** titles, **Return YouTube
  Dislike** counts — all optional
- **Playback queue**, autoplay countdown, shuffle, repeat, resume positions
- **WiFi watchdog**: if the network drops you get a clear reconnect panel,
  and everything retries automatically when it comes back — the app never
  freezes or quits on you
- **First-boot wizard**: language, device, quality, hardware decoding,
  SponsorBlock — then everything is tweakable in Settings

## Install

1. Copy the `pilastube` folder and `PilasTube.sh` into your ports
   directory (usually `/roms/ports/`, so you end up with
   `/roms/ports/pilastube/` and `/roms/ports/PilasTube.sh`).
2. That's it — nothing else to install. The built-in player uses the
   `ffmpeg` binary your firmware already ships (ArkOS etc.). If your
   firmware has no ffmpeg at all, PilasTube falls back to an external
   player automatically.
3. Launch **PilasTube** from EmulationStation / your front-end. The port
   opens with a terminal loading screen (`Loading... Please Wait.`), then
   the app takes over.
4. On first boot a short wizard asks for language, device, default
   quality, hardware decoding and SponsorBlock. Answers are saved in
   `pilastube/u_preferences.txt` — a plain `key=value` file you can edit
   by hand at any time.

**Upgrading from the old “YouTube” app or an older PilasTube?** Just
replace the folder — your favorites, history, subscriptions, watch
positions, preferences and login are migrated/kept automatically
(nothing is deleted from the old folder; your session survives updates).

## Sign in to your account (optional, recommended)

1. Settings → Account → **Sign in to YouTube**
2. The app shows a code and **yt.be/activate**
3. Open that link on your phone or PC, enter the code, press Allow
4. The console signs in automatically (spinner while it waits)

When signed in: the **Recommended** tab loads your account's home feed,
the **Subs** tab shows your subscription feed, the **History** tab shows
your YouTube watch history, and your subscribed channels are merged into
the channel list. The session is a refresh token stored in
`pilastube/oauth_token.json` — it survives reboots and app updates until
you sign out (Settings → Account → Sign out, or delete the file). You can
revoke it any time from your Google account settings.

Works without an account too: everything except the personalized feeds
functions logged-out (Recommended then falls back to trending).

## Controls

### Browsing (lists)
| Button | Action |
|---|---|
| D-pad | Navigate the list; left/right also switch tabs |
| **L2 / R2 (triggers)** | **Previous / next section (tab) — everywhere outside the player, including escaping Settings** |
| A | Play video (auto-resumes where you left off); open a category tile; **retry** when a feed failed |
| B | Back (from a category to the tiles; never exits the app) |
| X | Search |
| Y | Favorite / unfavorite |
| SELECT | Queue screen |
| START | Video menu (context menu) |
| **START + SELECT together** | **Exit PilasTube (the only way out)** |

### Categories tab
| Button | Action |
|---|---|
| D-pad up/down | Move one tile row (all ten tiles are reachable) |
| D-pad left/right | Move between the two tile columns |
| A | Open the category's videos |
| B | From a category's videos back to the tiles |

### Video menu (START)
Play / Resume / Play from beginning / Add to Queue / Play Next /
Play All From Here / Favorite / Go to Channel / Subscribe / Block
Channel / Video Info / Remove from History.

### Settings (two-pane — never scrolls)
| Button | Action |
|---|---|
| D-pad up/down | Move within the focused pane (sections left / rows right) |
| D-pad right / left | Open a section's rows / back to the section list |
| **A** | Row: **open the value picker** (settings change only via the picker) or run an action |
| B | Close picker → back to sections → exit Settings |
| L/R bumpers | Jump between sections |
| **L2 / R2** | Leave Settings directly |

Sections: Account, General, Playback, Audio & Subtitles, SponsorBlock,
Content, Network, Data. Notable options: quality cap, video codec
(H.264/VP9/AV1), audio language, network route
(Default/Cronet/OkHttp), proxy, **Update yt-dlp Now** (run this first if
videos ever stop working — YouTube changes its internals regularly).

### Sign-in screen
| Button | Action |
|---|---|
| A on **Sign in to YouTube** | Shows the code + yt.be/activate, waits, signs in |
| B while the code shows | Cancel the sign-in |
| A on **Sign out** | Removes the stored token (local data untouched) |

### During playback (built-in player — SmartTube mapping)

**HUD hidden (just watching):**

| Button | Action |
|---|---|
| **A** | **Pause / resume** (big on-screen pause badge) |
| **B** | **Exit to the menu** (instant) |
| **DOWN** | **Show the player UI** (progress bar + options row) |
| D-pad left/right | Seek −/+ (step configurable in Settings) |
| D-pad up | Chapter list (when chapters exist) / volume + |
| L2 / R2 (triggers) | Volume −/+ anytime |
| L3 / R3 (stick clicks) | Previous / next chapter |
| L1 / R1 | Previous / next video |
| X | Quality menu (switches without losing your position) |
| Y | Speed menu (0.5× – 2×) |
| START | Options menu (captions, loop, volume, stats, chapters, channel, queue) |
| SELECT | Show elapsed / remaining time |

**HUD visible (after DOWN):**

| Button | Action |
|---|---|
| D-pad / stick left/right | Move the focus through the options row (prev, −10 s, play/pause, +10 s, next, CC, speed, quality, chapters/loop, options) |
| **A** | **Activate the focused option** |
| **B / UP / DOWN** | Hide the player UI |
| **START + SELECT together** | **Exit PilasTube (the only way out)** |

When a video ends and autoplay/queue has a next video: **A** plays it
now, **B** cancels the countdown.

## Logs — read this when something goes wrong

PilasTube records everything into two files inside the port folder:

| File | What it contains |
|---|---|
| `pilastube/logs/simple.txt` | Plain-language one-liners: app started, wizard finished, searching, playing, closed normally / crashed (with reason) |
| `pilastube/logs/detailed.txt` | Full technical log: environment snapshot, every startup step, feed diagnostics (HTTP status / item counts), player output, crash tracebacks |

Logs rotate automatically (`.old` backup) so they never fill your SD
card. If anything misbehaves, **send `logs/detailed.txt`** — it says
exactly which layer failed.

## Troubleshooting

- **App does not open**: the launcher prints the reason on the terminal
  for a few seconds; the last lines of `logs/detailed.txt` name the
  failing layer (Python, SDL2, window, renderer, font, yt-dlp).
- **Videos stop working / “Failed to get URL”**: YouTube changed
  something. Settings → Network → **Update yt-dlp Now**; if it persists,
  try switching **YT Client** (web / tv / android / ios).
- **Feeds empty**: press A to retry (the app already walked its fallback
  chain). Check the WiFi icon (red = offline) and the `[FEED]` /
  `[browse]` lines in `logs/detailed.txt`.
- **Signed in but feeds look logged-out**: check the `[browse]` lines in
  `logs/detailed.txt` — they show the HTTP status and video count of
  every account feed request.
- **Stuttering playback**: keep quality at Auto/480p (software decode;
  480p is the sweet spot at 640×480). The `stats for nerds` overlay
  (player options) shows dropped frames.
- **No sound**: `[AUDIO]` lines in `logs/detailed.txt` explain it; the
  app must run as your console user (the launcher handles this).
- **Seeking takes a second**: the player restarts the stream at the new
  position — normal for direct-URL playback.

## Files in the port folder

| File | Purpose |
|---|---|
| `PilasTube.py` | Application core (state, data, settings, input, main loop) |
| `player.py` | Built-in player engine + player HUD + queue |
| `ui.py` | All main-screen renderers + drawing toolkit |
| `auth.py` | Account sign-in / restore / feeds |
| `utils.py` | Logging, SDL2 bootstrap, translations, shared constants |
| `yt_extras.py` | YouTube data/network layer (feeds, formats, SponsorBlock…) |
| `logo.png` / `logo.webp` | The PilasTube logo (2172×724) used by this README |
| `u_preferences.txt` | Your preferences (wizard + Settings) — human-editable |
| `oauth_token.json` | YouTube account session (created when you sign in) |
| `yt_subs.json` / `yt_favorites.json` / `yt_history.json` | Subscriptions, favorites, history |
| `yt_positions.json` / `yt_seen.json` / `yt_searches.json` / `yt_blocked.json` | Resume positions, seen-tracking, recent searches, blocked channels |
| `.thumb_cache/` | Thumbnail cache (clearable in Settings) |
| `logs/` | simple.txt + detailed.txt (see above) |

## Credits

- **PilasTube** — project direction & on-device testing: Jackson Manfredo;
  engineered with [Super Z](https://z.ai) (Z.ai)
- **[SmartTube](https://github.com/yuliskov/SmartTube)** by yuliskov —
  the feature blueprint and the TV request shapes this app follows
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** — stream extraction
  (a standalone aarch64 build is bundled)
- **ffmpeg** — the playback engine of the built-in player
- **SDL2 / PySDL2** — rendering, audio and gamepad input
- **[SponsorBlock](https://sponsor.ajay.app)**,
  **[DeArrow](https://dearrow.ajay.app)**,
  **[Return YouTube Dislike](https://returnyoutubedislike.com)** —
  crowd-sourced metadata APIs
- **PortMaster & the r36swiki.com community** — the platform and the
  original port this all started from
- The terminal boot screen is an homage to DDLC's famous opening.
  *Just Monika.*

## Version history

> **Numbering note:** this release was previously numbered “3.7”; the
> project now uses **0.MINOR.PATCH** — so 3.7 → 0.3.7, 3.6 → 0.3.6, and
> so on. Same lineage, clearer numbers.

### What's new in 0.3.7

- **Account system rebuilt** — history, subscriptions and
  recommendations work again: YouTube migrated the TV feeds to a new
  card format (`tileRenderer`) the old parser couldn't read (everything
  parsed to zero items, so every tab silently fell back). The parser now
  speaks the new format (plus the classic ones), verified against real
  responses and SmartTube's own captures.
- **Requests now look exactly like the real TV app** — full `tvAppInfo`
  context, live visitorData, Cobalt Fire-TV user agent (SmartTube's
  request shape), and brand-account identity via `X-Goog-Pageid`.
- **Recommended refreshes right after sign-in** (no more stale trending
  list on the first tab), and channel import works again (TV guide
  navigation).
- **Terminal loading screen** — `Loading... Please Wait.` + the PILAS
  logo on the terminal before the app opens, with a clear failure
  message and proper signal handling if something goes wrong.
- **Feed diagnostics** — every account feed request logs its HTTP
  status and video count to `logs/detailed.txt`, so future YouTube
  changes are visible immediately.

| Version | Highlights |
|---|---|
| **0.3.7** | Account feeds rebuilt for YouTube's new TV format; TV-app identity; terminal boot screen; feed diagnostics |
| **0.3.6** | Category grid navigation; audio-language selection; SUG-key suggestions; Recommended feed + localized titles fixed; search pages (16/page); WiFi watchdog |
| **0.3.5** | Modularized into six modules — no behaviour change |
| **0.3.4** | Live-stream audio fixed; Recommended + Categories tabs; L2/R2 section switching; “Desligado” picker bug |
| **0.3.3** | Two-pane Settings (never scrolls); account login via yt.be/activate |
| **0.3.2** | Playback consistency (colors, clock, seeks); codec + network-route selectors |
| **0.3.1** | Video pipeline + input overhaul (bluish tint, racing progress bar, navigable HUD) |
| **0.3.0** | Built-in player — no-sound and black-screen-after-B fixed |
| **0.2.2** | Exit only via START+SELECT; trending fallback chain; WiFi indicator |
| **0.2.1** | Crash logging + hardened startup |
| **0.2.0** | The SmartTube-inspired wave: subscriptions, SponsorBlock, queue, quality engine, first-boot wizard |
| **0.1.0** | The original community port |

---

*PilasTube is an unofficial client. Streams are extracted with yt-dlp for
personal use; the app never shows server-side ads and never requires a
Google account. YouTube is a trademark of Google LLC — this project is
not affiliated with Google or YouTube.*
