#!/bin/bash

# ============================================================================
#  PilasTube v0.3.7 - PortMaster Launch Script
#  YouTube client for handhelds (SmartTube-inspired).
#
#  v0.3.5: the app is modular - six modules live in pilastube/:
#    PilasTube.py (core) + player.py + ui.py + auth.py + utils.py +
#    yt_extras.py (the launcher starts PilasTube.py)
#  v0.3.6: category grid UP/DOWN navigation, audio language selection
#    (settings + in-player), on-demand search suggestions (SUG key),
#    fixed Recommended feed (FEwhat_to_watch), language-correct titles,
#    16-per-page search results, WiFi reconnect watchdog.
#  v0.3.7: account system rebuilt (SmartTube's full TV browse context:
#    tvAppInfo/zylon + visitorData + X-Goog-Pageid brand identity ->
#    recommendations, subscriptions and history work again) + the
#    DDLC-style terminal boot screen (Loading... Please Wait. + PILAS
#    logo + star art on /dev/tty0, failure/restart messages, signals).
#
#  Hardened launch path with full logging:
#    - absolute paths derived from this script's own location
#    - PortMaster auto-detection across all known firmware locations
#    - environment snapshot written to logs/detailed.txt before Python starts
#    - XDG_RUNTIME_DIR created (mode 700) when missing
#    - legacy "youtube" app data migration (favorites / history / prefs)
#    - app stdout+stderr appended live into logs/detailed.txt (python -u)
#    - exit code + crash tail recorded; clear message on the terminal
# ============================================================================

# ---------------------------------------------------------------------------
# 0. Absolute locations (NEVER rely on the current working directory)
# ---------------------------------------------------------------------------
PORT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$PORT_DIR/pilastube"
LOG_DIR="$APP_DIR/logs"
DETAILED="$LOG_DIR/detailed.txt"
SIMPLE="$LOG_DIR/simple.txt"

mkdir -p "$LOG_DIR" 2>/dev/null

ts() { date '+%Y-%m-%d %H:%M:%S'; }

LOG() {                            # detailed technical line
    echo "[$(ts)] [LAUNCHER] $*" >> "$DETAILED" 2>/dev/null
}
SLOG() {                           # simple human line
    echo "[$(ts)] $*" >> "$SIMPLE" 2>/dev/null
}

# ---------------------------------------------------------------------------
# v0.3.7 TTY helpers - the DDLC-style terminal boot screen
# (TERMINAL_BOOT_REPORT: what the player sees is written directly to
# /dev/tty0; the real debug transcript lives in logs/detailed.txt)
# ---------------------------------------------------------------------------
pm_tty_chmod() {
    # make /dev/tty0 writable so direct writes work on every firmware
    if [ -e /dev/tty0 ]; then
        if [ -n "${ESUDO:-}" ]; then
            $ESUDO chmod 666 /dev/tty0 2>/dev/null
        else
            chmod 666 /dev/tty0 2>/dev/null
        fi
    fi
}

pm_tty_clear() {
    printf '\033c' > /dev/tty0 2>/dev/null
}

pm_tty_message() {                 # short visible message + log pointer
    pm_tty_clear
    {
        echo "$1"
        [ -n "$2" ] && echo "$2"
    } > /dev/tty0 2>/dev/null
}

pm_tty_splash() {                  # the boot/loading screen
    pm_tty_chmod
    pm_tty_clear
    # compact ASCII splash (fits small 4:3 terminals, pure ASCII - no
    # broken encoding on handheld firmwares)
    cat > /dev/tty0 2>/dev/null <<'EOF'
Loading... Please Wait.

 ____ ___ _     _    ____
|  _ \_ _| |   / \  / ___|
| |_) | || |  / _ \ \___ \
|  __/| || |_| ___ \ ___) |
|_|  |___|____/_/ \_\____/

       /\
      //\\
 ____//__\\____
 \.-//----\\-,/
  \v/      \v/
  /\\      //\
 //_\\____//_\\
'----\\--//----`
      \//
       \/

EOF
}

pm_tty_failed() {                  # crash exit message (DDLC failure path)
    pm_tty_message "PilasTube failed." \
                   "Check pilastube/logs/detailed.txt."
}

# rotate logs if they grew too big (keep one previous generation)
rotate() {
    local f="$1" max="$2"
    if [ -f "$f" ] && [ "$(wc -c < "$f" 2>/dev/null || echo 0)" -gt "$max" ]; then
        mv -f "$f" "$f.old" 2>/dev/null
    fi
}
rotate "$DETAILED" 524288          # 512 KB
rotate "$SIMPLE"   131072          # 128 KB

LOG "================ PilasTube launch begins ================"
LOG "script: ${BASH_SOURCE[0]}"
LOG "port dir: $PORT_DIR"
LOG "app dir: $APP_DIR"
SLOG "PilasTube starting"

# signals are crash exits while the app runs (DDLC trap pattern)
trap 'LOG "terminated by SIGHUP"; pm_tty_failed; exit 129' HUP
trap 'LOG "terminated by SIGINT";  pm_tty_failed; exit 130' INT
trap 'LOG "terminated by SIGTERM"; pm_tty_failed; exit 143' TERM

# ---------------------------------------------------------------------------
# 1. Sanity check the app folder before anything else
# ---------------------------------------------------------------------------
for NEED in PilasTube.py player.py ui.py auth.py utils.py yt_extras.py; do
    if [ ! -f "$APP_DIR/$NEED" ]; then
        LOG "FATAL: $APP_DIR/$NEED not found - bad install layout"
        SLOG "Failed to start: app folder incomplete"
        pm_tty_message "PilasTube failed." \
                       "pilastube/$NEED missing next to PilasTube.sh"
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# 2. Migrate user data from the older "youtube" app installs (v1 / v2.0)
# ---------------------------------------------------------------------------
for OLD_DIR in "/roms/ports/youtube" "$PORT_DIR/youtube"; do
    if [ -d "$OLD_DIR" ] && [ "$OLD_DIR" != "$APP_DIR" ]; then
        for f in yt_favorites.json yt_history.json yt_positions.json \
                 yt_blocked.json yt_searches.json yt_subs.json \
                 yt_seen.json u_preferences.txt oauth_token.json; do
            if [ -f "$OLD_DIR/$f" ] && [ ! -f "$APP_DIR/$f" ]; then
                cp -f "$OLD_DIR/$f" "$APP_DIR/$f" 2>/dev/null \
                    && LOG "migrated $f from $OLD_DIR" \
                    && SLOG "Imported old data: $f"
            fi
        done
        # keep old favourites thumbnails so the first browse is instant
        if [ -d "$OLD_DIR/.thumb_cache" ] && [ ! -d "$APP_DIR/.thumb_cache" ]; then
            cp -rf "$OLD_DIR/.thumb_cache" "$APP_DIR/.thumb_cache" 2>/dev/null \
                && LOG "migrated .thumb_cache from $OLD_DIR"
        fi
    fi
done

# ---------------------------------------------------------------------------
# 3. Locate PortMaster (all known firmware layouts)
# ---------------------------------------------------------------------------
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
controlfolder=""
for candidate in \
    "/opt/system/Tools/PortMaster" \
    "/opt/tools/PortMaster" \
    "$XDG_DATA_HOME/PortMaster" \
    "/roms/ports/PortMaster" \
    "/roms/ports/PortMaster/PortMaster"; do
    if [ -f "${candidate}/control.txt" ]; then
        controlfolder="$candidate"
        break
    fi
done

if [ -n "$controlfolder" ]; then
    # shellcheck disable=SC1090
    source "${controlfolder}/control.txt"
    if [ -f "${controlfolder}/mod_${CFW_NAME}.txt" ]; then
        # shellcheck disable=SC1090
        source "${controlfolder}/mod_${CFW_NAME}.txt"
    fi
    LOG "PortMaster found: $controlfolder (CFW_NAME=${CFW_NAME:-unknown})"
    LOG "ESUDO=${ESUDO:-<unset>}"
else
    LOG "WARN: PortMaster control.txt not found - continuing with system defaults"
    SLOG "Warning: PortMaster not found"
fi

# ---------------------------------------------------------------------------
# 4. Environment
# ---------------------------------------------------------------------------
if [ -n "$controlfolder" ]; then
    export LD_LIBRARY_PATH="$controlfolder/libs:$controlfolder/utils/lib:$LD_LIBRARY_PATH"
    export PYTHONPATH="$APP_DIR:$controlfolder/exlibs:$controlfolder/pylibs:$controlfolder/libs:$PYTHONPATH"
    export PYSDL2_DLL_PATH="$controlfolder/libs"
else
    export PYTHONPATH="$APP_DIR:$PYTHONPATH"
fi
export PATH="$HOME/.local/bin:/home/ark/.local/bin:$PATH"

# XDG_RUNTIME_DIR: needed by PulseAudio/PipeWire AND KMS/DRM.
# v3.0: if the system already gave the current user one, KEEP IT - that is
# where the user's audio server socket lives. Only create our own when the
# firmware provided nothing (v2.2 always replaced it, which as root broke
# audio completely: "XDG_RUNTIME_DIR is not owned by us (uid 0)").
SYS_XDG="${XDG_RUNTIME_DIR:-}"
if [ -n "$SYS_XDG" ] && [ -d "$SYS_XDG" ] && [ -O "$SYS_XDG" ]; then
    LOG "using system XDG_RUNTIME_DIR=$SYS_XDG (owned by $(id -un))"
else
    export XDG_RUNTIME_DIR="/tmp/pilastube_runtime"
    mkdir -p "$XDG_RUNTIME_DIR" 2>/dev/null
    chmod 700 "$XDG_RUNTIME_DIR" 2>/dev/null
    LOG "created XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR (mode 700)"
fi

# make sure bundled tools are executable
chmod +x "$APP_DIR/yt-dlp" 2>/dev/null
[ -n "${ESUDO:-}" ] && $ESUDO chmod +x "$APP_DIR/yt-dlp" 2>/dev/null

# terminal settings
pm_tty_chmod
export TERM=linux
pm_tty_clear

# ---------------------------------------------------------------------------
# 5. Environment snapshot (the exact launch context, for diagnostics)
# ---------------------------------------------------------------------------
PY_BIN="$(command -v python3 || echo python3)"
LOG "python: $PY_BIN ($($PY_BIN --version 2>&1))"
LOG "uid=$(id -u) user=$(id -un 2>/dev/null || echo '?')"
LOG "DISPLAY=${DISPLAY:-<unset>} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-<unset>}"
LOG "SDL_VIDEODRIVER=${SDL_VIDEODRIVER:-<unset>} SDL_AUDIODRIVER=${SDL_AUDIODRIVER:-<unset>}"
LOG "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}"
LOG "PYSDL2_DLL_PATH=${PYSDL2_DLL_PATH:-<unset>}"
LOG "LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"
LOG "PYTHONPATH=${PYTHONPATH:-<unset>}"
LOG "cwd=$(pwd) target=$APP_DIR/PilasTube.py"

# ---------------------------------------------------------------------------
# 6. Launch the app
#
# v3.0 AUDIO FIX: run as the regular console user, NOT root. PulseAudio /
# PipeWire on these firmwares runs as the logged-in user and refuses root
# clients, so the old "$ESUDO python3" launch played video with NO SOUND.
# The sudo path stays only as a fallback for when the current user cannot
# open the DRM card (video matters more than audio).
# ---------------------------------------------------------------------------
DRM_CARD="/dev/dri/card0"
RUN_CMD=""
if [ "$(id -u)" = "0" ]; then
    LOG "running as root (inherited) - audio may be unavailable"
elif [ -c "$DRM_CARD" ] && [ ! -w "$DRM_CARD" ]; then
    if [ -n "${ESUDO:-}" ]; then
        LOG "WARN: $DRM_CARD not writable by $(id -un) - using sudo fallback"
        LOG "       (video OK, audio may be silent)"
        SLOG "Warning: running as root - sound may not work"
        RUN_CMD="$ESUDO --preserve-env=PATH,PYTHONPATH,PYSDL2_DLL_PATH,LD_LIBRARY_PATH,XDG_RUNTIME_DIR,PULSE_SERVER,PULSE_COOKIE,SDL_GAMECONTROLLERCONFIG_FILE,DEVICE,param_device,HOTKEY,ANALOGSTICKS,SDL_KMSDRM_ORIENTATION,SDL_KMSDRM_ROTATION"
        # best effort: point root at the console user's pulse socket+cookie
        ORIG_UID="$(stat -c %u "$HOME" 2>/dev/null || echo 1000)"
        export PULSE_SERVER="unix:/run/user/$ORIG_UID/pulse/native"
        [ -f "$HOME/.config/pulse/cookie" ] && \
            export PULSE_COOKIE="$HOME/.config/pulse/cookie"
        LOG "root fallback audio env: PULSE_SERVER=$PULSE_SERVER"
    else
        LOG "WARN: $DRM_CARD not writable and no ESUDO available"
    fi
else
    LOG "running as $(id -un) (direct) - audio + video fully supported"
fi
LOG "exec: ${RUN_CMD:-<direct>} python3 -u PilasTube.py (stdout+stderr -> detailed.txt)"

cd "$APP_DIR" || {
    LOG "FATAL: cannot cd into $APP_DIR"
    SLOG "Failed to start: cannot enter app folder"
    pm_tty_failed
    exit 1
}

# v0.3.7: draw the terminal loading splash right before the app takes over
# the screen (DDLC phase 5: the player sees "Loading... Please Wait." +
# the compact PILAS logo while the SDL window opens)
pm_tty_splash

${RUN_CMD:-} python3 -u "$APP_DIR/PilasTube.py" >> "$DETAILED" 2>&1
RC=$?

LOG "app exited with code $RC"
if [ "$RC" -eq 0 ]; then
    SLOG "PilasTube closed normally"
else
    SLOG "PilasTube exited with error (code $RC) - see logs/detailed.txt"
    # DDLC failure path: clear the TTY, show the two-line failure message
    # (the full transcript is already in logs/detailed.txt)
    pm_tty_failed
    sleep 3 2>/dev/null
fi

# ---------------------------------------------------------------------------
# 7. Cleanup
# ---------------------------------------------------------------------------
# DDLC success path: the player returns to the firmware UI, TTY cleared
pm_tty_clear
exit $RC
