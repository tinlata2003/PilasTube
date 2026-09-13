# -*- coding: utf-8 -*-
"""PilasTube bootstrap for ROCKNIX/PortMaster.

Provides a known Unicode TTF when the firmware has no usable system font and
adds the Vietnamese UI language without requiring a binary font in Git.
"""

import importlib.abc
import importlib.machinery
import os
import ssl
import urllib.request
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
_FONT = os.path.join(_BASE, "font.ttf")
_FONT_URLS = (
    "https://github.com/notofonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf",
    "https://raw.githubusercontent.com/notofonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf",
)


def _valid_font(path):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 16384:
            return False
        with open(path, "rb") as f:
            return f.read(4) in (b"\x00\x01\x00\x00", b"true", b"OTTO")
    except Exception:
        return False


def _download_font():
    for url in _FONT_URLS:
        tmp = _FONT + ".download"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PilasTube/0.3.9"})
            with urllib.request.urlopen(
                req, timeout=20, context=ssl._create_unverified_context()
            ) as r:
                data = r.read()
            if len(data) < 16384 or data[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
                continue
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, _FONT)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return False


def _find_existing_font():
    candidates = [
        os.environ.get("PILASTUBE_FONT"),
        _FONT,
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/opt/system/Tools/PortMaster/themes/default.ttf",
        "/opt/system/Tools/PortMaster/themes/ThemeDefault.ttf",
    ]
    for path in candidates:
        if path and _valid_font(path):
            return path
    return None


_FONT_PATH = _find_existing_font()
if not _FONT_PATH and _download_font():
    _FONT_PATH = _FONT

if _FONT_PATH:
    os.environ["PILASTUBE_FONT_PATH"] = _FONT_PATH
    try:
        print("[FONT] usable TTF: %s (%d bytes)" %
              (_FONT_PATH, os.path.getsize(_FONT_PATH)))
    except Exception:
        pass
else:
    print("[FONT] ERROR: no usable TTF found and download failed")


# ---------------------------------------------------------------------------
# Vietnamese localization
# ---------------------------------------------------------------------------
# The main project keeps translations in utils.py. We inject the Vietnamese
# table after utils has executed so this small bootstrap remains independent
# of the large application source and is safe across future updates.
_VI = {
    "nav_home": "Thịnh hành", "nav_subs": "Kênh đăng ký", "nav_search": "Tìm kiếm",
    "nav_favorites": "Yêu thích", "nav_history": "Lịch sử", "nav_settings": "Cài đặt",
    "nav_recommended": "Dành cho bạn", "nav_categories": "Danh mục",
    "cat_trending": "Thịnh hành", "cat_music": "Âm nhạc", "cat_gaming": "Game",
    "cat_live": "Trực tiếp", "cat_movies": "Phim", "cat_news": "Tin tức",
    "cat_sports": "Thể thao", "cat_learning": "Học tập", "cat_podcasts": "Podcast",
    "cat_favorites": "Yêu thích",
    "msg_select_category": "Chọn một danh mục",
    "msg_cat_empty": "Chưa có video trong danh mục này",
    "settings_title": "Cài đặt",
    "sec_general": "CHUNG", "sec_playback": "PHÁT VIDEO",
    "sec_sponsorblock": "SPONSORBLOCK & TIỆN ÍCH", "sec_content": "BỘ LỌC NỘI DUNG",
    "sec_network": "MẠNG / TUYẾN KẾT NỐI", "sec_data": "DỮ LIỆU",
    "settings_language": "Ngôn ngữ", "settings_theme": "Giao diện",
    "settings_search_count": "Số kết quả mỗi trang", "settings_auto_load": "Tự tải trang chủ",
    "settings_quality": "Chất lượng video", "settings_hwdec": "Giải mã phần cứng",
    "settings_codec": "Codec video", "settings_route": "Tuyến mạng",
    "settings_seek_interval": "Bước tua (Trái/Phải)", "settings_playback_mode": "Chế độ phát",
    "settings_speed_memory": "Nhớ tốc độ phát", "settings_subtitles": "Phụ đề",
    "settings_sleep_timer": "Hẹn giờ ngủ", "settings_volume_boost": "Tăng âm lượng",
    "settings_sponsorblock": "SponsorBlock", "settings_dearrow": "Tiêu đề DeArrow",
    "settings_ryd": "Số lượt không thích", "settings_suggestions": "Gợi ý tìm kiếm",
    "settings_hide_shorts": "Ẩn Shorts", "settings_hide_live": "Ẩn video trực tiếp",
    "settings_hide_watched": "Ẩn video đã xem", "settings_proxy": "URL Proxy",
    "settings_prefer_ipv4": "Ưu tiên IPv4", "settings_player_client": "YouTube Client",
    "settings_socket_timeout": "Thời gian chờ mạng", "settings_ytdlp_update": "Cập nhật yt-dlp ngay",
    "settings_ytdlp_ver": "Phiên bản yt-dlp", "settings_clear_favorites": "Xóa mục yêu thích",
    "settings_clear_history": "Xóa lịch sử", "settings_clear_cache": "Xóa cache hình thu nhỏ",
    "settings_clear_positions": "Xóa vị trí xem", "settings_clear_searches": "Xóa lịch sử tìm kiếm",
    "settings_clear_blocked": "Xóa kênh đã chặn", "settings_reset": "Đặt lại toàn bộ cài đặt",
    "settings_execute": "[A] Thực hiện", "settings_edit": "[A] Sửa",
    "settings_on": "Bật", "settings_off": "Tắt", "settings_min": "phút",
    "msg_searching": "Đang tìm video...", "msg_loading": "Đang tải...",
    "msg_loading_video": "Đang tải video...", "msg_loading_videos": "Đang tải các video...",
    "msg_resuming": "Tiếp tục từ", "msg_no_results": "Không có kết quả",
    "msg_press_search": "Nhấn X để tìm kiếm", "msg_no_ytdlp": "Không tìm thấy yt-dlp!",
    "msg_no_player": "Không có trình phát video!", "msg_install_ytdlp": "Cài đặt: pip install yt-dlp",
    "msg_install_player": "Cài đặt: mpv hoặc ffplay", "msg_timeout": "Hết thời gian chờ",
    "msg_added_fav": "Đã thêm vào yêu thích", "msg_removed_fav": "Đã xóa khỏi yêu thích",
    "msg_fav_cleared": "Đã xóa mục yêu thích", "msg_history_cleared": "Đã xóa lịch sử",
    "msg_cache_cleared": "Đã xóa cache", "msg_videos": "video",
    "msg_positions_cleared": "Đã xóa vị trí xem", "msg_searches_cleared": "Đã xóa lịch sử tìm kiếm",
    "msg_blocked_cleared": "Đã xóa danh sách kênh chặn", "msg_prefs_reset": "Đã đặt lại cài đặt",
    "msg_subscribed": "Đã đăng ký kênh!", "msg_unsubscribed": "Đã hủy đăng ký",
    "msg_blocked": "Đã chặn kênh", "msg_unblocked": "Đã bỏ chặn kênh",
    "msg_queue_added": "Đã thêm vào hàng đợi", "msg_queue_next": "Sẽ phát tiếp theo",
    "msg_play_all": "Đang phát phần còn lại", "msg_no_channel": "Không có thông tin kênh",
    "msg_skipped": "Đã bỏ qua", "msg_next": "Tiếp theo",
    "msg_updating": "Đang cập nhật yt-dlp...", "msg_update_ok": "Đã cập nhật yt-dlp",
    "update_title": "Có bản cập nhật yt-dlp", "update_current": "Phiên bản hiện tại",
    "update_latest": "Phiên bản mới", "update_ask": "Cập nhật ngay?",
    "update_yes": "Có - cập nhật ngay", "update_no": "Không - bỏ qua",
    "update_skipped": "Đã bỏ qua cập nhật (Cài đặt > Mạng)",
    "update_offline": "Bỏ qua kiểm tra cập nhật (mất mạng)",
    "msg_sb_off": "SponsorBlock đã tắt", "msg_empty_subs": "Chưa có kênh đăng ký",
    "msg_subs_hint": "Mở video > START > Đăng ký", "help_keyboard": "A:Gõ  B:Đóng  SUG:Gợi ý  START:Tìm  Y:Xóa",
    "help_main": "A:Chọn  X:Tìm  Y:Yêu thích  L2/R2:Mục  START+SELECT:Thoát",
    "help_ctx": "A:OK  B:Đóng", "help_queue": "A:Phát  Y:Xóa  B:Quay lại",
    "kb_space": "KHOẢNG TRẮNG", "kb_go": "ĐI", "kb_sym": "KÝ HIỆU", "kb_abc": "abc",
    "kb_search_placeholder": "Nhập để tìm kiếm...", "kb_proxy_placeholder": "URL Proxy (trống = trực tiếp)",
    "time_today": "Hôm nay", "time_live": "TRỰC TIẾP", "badge_new": "MỚI",
    "ctx_title": "Menu video", "ctx_play": "Phát", "ctx_resume": "Tiếp tục từ",
    "ctx_beginning": "Phát từ đầu", "ctx_queue_add": "Thêm vào hàng đợi",
    "ctx_queue_next": "Phát tiếp theo", "ctx_play_all": "Phát tất cả từ đây",
    "ctx_channel": "Mở kênh", "ctx_subscribe": "Đăng ký kênh", "ctx_unsubscribe": "Hủy đăng ký",
    "ctx_block": "Chặn kênh", "ctx_unblock": "Bỏ chặn kênh", "ctx_info": "Thông tin video",
    "ctx_remove_history": "Xóa khỏi lịch sử", "queue_title": "Hàng đợi phát", "queue_empty": "Hàng đợi trống",
    "channel_title": "Kênh", "sugg_title": "Gợi ý", "sugg_hist": "Tìm kiếm gần đây",
    "wiz_welcome": "Chào mừng! Thiết lập lần đầu", "wiz_welcome_sub": "Một vài câu hỏi để tối ưu ứng dụng cho thiết bị của bạn",
    "wiz_language": "Ngôn ngữ / Language", "wiz_device": "Thiết bị của bạn",
    "wiz_quality": "Chất lượng video mặc định", "wiz_hwdec": "Giải mã video bằng phần cứng",
    "wiz_sb": "SponsorBlock (tự động bỏ qua đoạn quảng cáo)", "wiz_done": "Thiết lập hoàn tất!",
    "wiz_done_sub": "Bạn có thể thay đổi mọi thứ sau trong Cài đặt",
    "wiz_hint": "D-pad: chọn   A: tiếp   B: quay lại", "wiz_finish": "Bắt đầu dùng PilasTube",
    "msg_exit_hint": "Nhấn START + SELECT để thoát", "msg_feed_failed": "Không thể tải video",
    "msg_retry": "[A] Thử lại", "msg_no_internet": "Không có kết nối Internet",
    "msg_err_ignored": "Đã bỏ qua lỗi - xem logs", "msg_cached": "đã lưu cache",
    "msg_reconnecting": "Đang kết nối lại WiFi...", "msg_back_online": "Đã khôi phục kết nối",
    "msg_net_attempts": "Lần thử %s", "msg_page": "Trang", "msg_end_results": "Đã hết kết quả",
    "settings_audio_lang": "Ngôn ngữ âm thanh", "player_audio_lang": "Âm thanh",
    "player_audio_single": "Một track âm thanh", "kb_sug": "GỢI Ý",
    "player_pause": "Tạm dừng", "player_play": "Phát", "player_prev": "Trước", "player_next": "Tiếp theo",
    "player_quality": "Chất lượng", "player_speed": "Tốc độ", "player_codec": "Codec",
    "player_captions": "Phụ đề", "player_loop": "Lặp lại", "player_volume": "Âm lượng",
    "player_stats": "Thống kê", "player_chapters": "Chương", "player_options": "Tùy chọn",
    "player_no_chapters": "Không có chương", "player_no_next": "Không có video tiếp theo",
    "player_open_channel": "Mở kênh", "player_buffering": "Đang tải bộ đệm...",
    "player_seeking": "Đang tua...", "player_next_in": "Tiếp theo sau",
    "player_countdown_hint": "A: phát ngay   B: hủy", "player_sleep_stop": "Hẹn giờ ngủ - đang dừng",
    "player_hint": "A:tạm dừng  B:thoát  </>:tua  v:điều khiển  START:tùy chọn",
    "player_hint_ui": "</>: di chuyển  A: chọn  B: đóng  v: ẩn",
    "player_cc_off": "Tắt", "player_cc_none": "Không có phụ đề",
    "player_showtime_menu": "Hiện thời gian / vị trí", "player_stat_res": "Độ phân giải",
    "player_stat_shown": "Khung hình đã hiển thị", "player_stat_dropped": "Khung hình bị rớt",
    "player_stat_buffer": "Bộ đệm", "loop_off": "Tắt lặp lại",
    "sec_account": "TÀI KHOẢN", "sec_audio": "ÂM THANH & PHỤ ĐỀ",
    "settings_login": "Đăng nhập YouTube", "settings_logout": "Đăng xuất",
    "settings_signed_in_as": "Đã đăng nhập với", "settings_not_signed_in": "Chưa đăng nhập",
    "settings_pick": "Chọn", "settings_pick_hint": "A:OK   B:Hủy",
    "login_title": "Đăng nhập YouTube", "login_open": "Trên điện thoại hoặc PC, mở",
    "login_enter": "và nhập mã này", "login_waiting": "Đang chờ đăng nhập...",
    "login_success": "Đã đăng nhập với %s", "login_failed": "Đăng nhập thất bại",
    "login_denied": "Đăng nhập bị từ chối", "login_expired": "Mã đã hết hạn - thử lại",
    "login_cancel_hint": "B: Hủy", "msg_live_no_seek": "Video trực tiếp không thể tua",
    "history_source_yt": "YouTube", "history_source_local": "Thiết bị",
    "subs_account": "Tài khoản", "msg_account_feed_fail": "Không tải được nguồn video tài khoản",
}


class _UtilsLocaleLoader(importlib.abc.Loader):
    def __init__(self, original):
        self.original = original

    def create_module(self, spec):
        if hasattr(self.original, "create_module"):
            return self.original.create_module(spec)
        return None

    def exec_module(self, module):
        self.original.exec_module(module)
        try:
            module.TRANSLATIONS["Tiếng Việt"] = dict(_VI)
            if "Tiếng Việt" not in module.LANGUAGES:
                # Put Vietnamese immediately after English for easy discovery.
                module.LANGUAGES.insert(1, "Tiếng Việt")
            print("[LANG] Vietnamese UI enabled")
        except Exception as exc:
            print("[LANG] Vietnamese UI bootstrap failed: %s" % exc)


class _UtilsLocaleFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "utils":
            return None
        # Bypass this finder and let PathFinder locate the real utils.py.
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _UtilsLocaleLoader(spec.loader)
        return spec


# utils.py is imported by PilasTube.py after sitecustomize has run.
if not any(isinstance(x, _UtilsLocaleFinder) for x in sys.meta_path):
    sys.meta_path.insert(0, _UtilsLocaleFinder())
