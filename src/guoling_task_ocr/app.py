"""国令助手：识别任务区域中的物品名并复制到剪贴板。"""

from __future__ import annotations

import re
import json
import math
import os
import threading
import time
import tkinter as tk
import logging
import sys
import traceback
import ctypes
import webbrowser
import urllib.error
import urllib.request
from dataclasses import replace
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .cloud_ocr import CloudOcrClient, CloudOcrError, DEFAULT_MODEL as DEFAULT_CLOUD_OCR_MODEL
from .flash_alert import FlashEvent, FlashMonitor, list_visible_windows, matches_event, play_sound
from .market_query import MARKET_WEB_URL, MarketClient, MarketQueryError, MarketSession, flatten_listings
from .task_progress import (
    TaskObjective,
    TaskProgress,
    ParsedTaskProgress,
    find_task_name,
    find_role_name,
    filter_task_progress_records,
    infer_unread_task_step,
    is_role_name_candidate,
    load_task_progress,
    parse_task_objective,
    parse_task_progress,
    record_task_progress,
    save_task_progress,
    summarize_task_rounds,
    task_record_key,
    update_task_progress_position,
)

from PIL import Image, ImageEnhance, ImageGrab, ImageOps, ImageTk


def enable_per_monitor_dpi_awareness() -> None:
    """使窗口坐标与 PrintWindow 返回的物理像素一致，避免高 DPI 下右下角被截断。"""
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


enable_per_monitor_dpi_awareness()


PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"
if not DATA_DIR.is_dir():
    # Allows development runs from the pre-package layout during migration.
    DATA_DIR = PACKAGE_DIR
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else PACKAGE_DIR.parents[1]
BUNDLED_RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", RUNTIME_DIR))
CHANGELOG_PATH = BUNDLED_RESOURCE_DIR / "CHANGELOG.md"
GITHUB_REPOSITORY = "Maple-258/QQSG_GuoLingZhuShou"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases"
GITHUB_LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


def _persistent_data_dir() -> Path:
    """Return a writable location that survives one-file EXE extraction."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base_dir = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    app_data_dir = base_dir / "GuoLingZhuShou"
    try:
        app_data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return RUNTIME_DIR
    return app_data_dir


APP_DATA_DIR = _persistent_data_dir()
SETTINGS_PATH = APP_DATA_DIR / "settings.json"
OCR_MODEL_DIR = APP_DATA_DIR / "ocr_models"
LOG_PATH = APP_DATA_DIR / "ocr_app.log"
TASK_PROGRESS_PATH = APP_DATA_DIR / "task_progress.json"
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TASK_CROP = (0.80, 0.41, 0.985, 0.54)
CLOUD_TASK_CROP = (0.785, 0.385, 0.993, 0.55)
DEFAULT_HOTKEY = "ctrl+alt+g"
DEFAULT_AUTO_INTERVAL_SECONDS = 2.0
MIN_AUTO_INTERVAL_SECONDS = 0.5
MAX_AUTO_INTERVAL_SECONDS = 60.0
DEFAULT_FLASH_COOLDOWN_SECONDS = 3.0
MIN_FLASH_COOLDOWN_SECONDS = 0.0
MAX_FLASH_COOLDOWN_SECONDS = 60.0
FLASH_EVENT_QUEUE_SIZE = 256
FLASH_EVENTS_PER_TICK = 32
CAPTURE_METHODS = ("WGC（后台，高效）", "PrintWindow（兼容）")
TASK_OCR_SCALE = 4
TASK_OCR_MAX_PIXELS = 4_000_000
CLOUD_TASK_OCR_SCALE = 2
CLOUD_TASK_OCR_MAX_PIXELS = 1_000_000
# QQ SG places the player-name HUD at a stable location inside its client area.
# Ratios keep the crop aligned on windowed, 720p, and higher-resolution clients.
PLAYER_NAME_REGION = (0.015, 0.015, 0.30, 0.18)
PLAYER_OCR_SCALE = 3
PLAYER_OCR_MAX_PIXELS = 1_500_000
UNCHANGED_FRAME_THRESHOLD = 2.0
WGC_CAPTURE_TIMEOUT_SECONDS = 3.0
TASK_CONTEXT_TERMS = ("任务", "国令", "NPC", "需要", "收集", "提交", "消灭")
MONSTER_OBJECTIVE_TERMS = ("消灭", "灭怪物", "击败", "击杀", "打败", "剿灭", "杀死")
ITEM_ALIASES_PATH = DATA_DIR / "道具OCR纠错.json"
ROLE_ALIASES_PATH = DATA_DIR / "角色OCR纠错.json"
ITEM_VOCABULARY_PATH = DATA_DIR / "官方道具词表.json"
MONSTER_VOCABULARY_PATH = DATA_DIR / "官方怪物词表.json"
NPC_VOCABULARY_PATH = DATA_DIR / "官方NPC词表.json"
CUSTOM_ITEM_VOCABULARY_PATH = APP_DATA_DIR / "custom_item_vocabulary.json"
EXCLUDED_WORDS = {
    "任务追踪", "国令慕贤", "高级国令", "当前", "任务", "目标", "进度", "完成",
    "可用", "点击", "道具", "物品", "材料", "需求", "需要", "所需", "收集", "寻找",
    "上交", "提交", "获得", "NPC",
}
ITEM_PATTERNS = (
    r"(?:需求|需要|所需|物品|道具|材料|收集|寻找|上交|提交|获得)[：: \t]*([\u4e00-\u9fff]{2,8}(?:-\d+级)?)",
    r"([\u4e00-\u9fff]{2,8}(?:-\d+级)?)\s*\d+\s*/\s*\d+",
)

DEFAULT_SETTINGS: dict[str, str | float | bool | dict[str, str]] = {
    "capture_method": CAPTURE_METHODS[1],
    "interval_seconds": DEFAULT_AUTO_INTERVAL_SECONDS,
    "hotkey": DEFAULT_HOTKEY,
    "window_title": "",
    "flash_title_filter": "",
    "flash_target_mode": "window",
    "flash_window_title": "",
    "flash_sound_mode": "system",
    "flash_wav_path": "",
    "flash_cooldown_seconds": DEFAULT_FLASH_COOLDOWN_SECONDS,
    "flash_enabled": False,
    "market_account": "",
    "market_token": "",
    "market_user_id": "",
    "market_region": "得陇",
    "market_auto_query": False,
    "ocr_mode": "local",
    "cloud_ocr_token": "",
    "cloud_ocr_model": DEFAULT_CLOUD_OCR_MODEL,
    "cloud_ocr_api_url": "",
    "task_tracker_role": "",
    "window_role_bindings": {},
    "show_changelog_on_start": True,
}


def task_ocr_target_size(image_size: tuple[int, int]) -> tuple[int, int]:
    """Preserve the normal 4x OCR scale without allowing oversized task crops."""
    width, height = image_size
    scale = min(TASK_OCR_SCALE, math.sqrt(TASK_OCR_MAX_PIXELS / (width * height)))
    return max(1, round(width * scale)), max(1, round(height * scale))


def cloud_task_ocr_target_size(image_size: tuple[int, int]) -> tuple[int, int]:
    """Keep cloud uploads clear without sending the local four-times enlargement."""
    width, height = image_size
    scale = min(CLOUD_TASK_OCR_SCALE, math.sqrt(CLOUD_TASK_OCR_MAX_PIXELS / (width * height)))
    return max(1, round(width * scale)), max(1, round(height * scale))


def should_skip_unchanged_task(
    skip_unchanged: bool, signature: bytes, previous_signature: bytes | None
) -> bool:
    if not skip_unchanged or previous_signature is None:
        return False
    difference = sum(abs(left - right) for left, right in zip(signature, previous_signature)) / len(signature)
    return difference < UNCHANGED_FRAME_THRESHOLD


def load_user_settings(settings_path: Path = SETTINGS_PATH) -> dict[str, str | float | bool | dict[str, str]]:
    """Load validated user preferences without letting a bad file prevent startup."""
    settings = DEFAULT_SETTINGS.copy()
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return settings
    if not isinstance(payload, dict):
        return settings

    capture_method = payload.get("capture_method")
    if capture_method in CAPTURE_METHODS:
        settings["capture_method"] = capture_method

    try:
        interval_seconds = float(payload.get("interval_seconds"))
    except (TypeError, ValueError):
        interval_seconds = DEFAULT_AUTO_INTERVAL_SECONDS
    if MIN_AUTO_INTERVAL_SECONDS <= interval_seconds <= MAX_AUTO_INTERVAL_SECONDS:
        settings["interval_seconds"] = interval_seconds

    hotkey = payload.get("hotkey")
    if isinstance(hotkey, str) and hotkey.strip():
        settings["hotkey"] = hotkey.strip()

    window_title = payload.get("window_title")
    if isinstance(window_title, str):
        settings["window_title"] = window_title

    for key in (
        "flash_title_filter", "flash_window_title", "flash_wav_path", "market_account", "market_token", "market_user_id",
        "market_region", "task_tracker_role", "cloud_ocr_token", "cloud_ocr_model", "cloud_ocr_api_url",
    ):
        value = payload.get(key)
        if isinstance(value, str):
            settings[key] = value

    ocr_mode = payload.get("ocr_mode")
    if ocr_mode in {"local", "cloud"}:
        settings["ocr_mode"] = ocr_mode

    target_mode = payload.get("flash_target_mode")
    if target_mode in {"window", "keyword"}:
        settings["flash_target_mode"] = target_mode
    elif settings["flash_title_filter"] and not settings["flash_window_title"]:
        settings["flash_target_mode"] = "keyword"

    sound_mode = payload.get("flash_sound_mode")
    if sound_mode in {"system", "wav", "beep"}:
        settings["flash_sound_mode"] = sound_mode

    try:
        flash_cooldown = float(payload.get("flash_cooldown_seconds"))
    except (TypeError, ValueError):
        flash_cooldown = DEFAULT_FLASH_COOLDOWN_SECONDS
    if MIN_FLASH_COOLDOWN_SECONDS <= flash_cooldown <= MAX_FLASH_COOLDOWN_SECONDS:
        settings["flash_cooldown_seconds"] = flash_cooldown

    flash_enabled = payload.get("flash_enabled")
    if isinstance(flash_enabled, bool):
        settings["flash_enabled"] = flash_enabled

    market_auto_query = payload.get("market_auto_query")
    if isinstance(market_auto_query, bool):
        settings["market_auto_query"] = market_auto_query

    show_changelog_on_start = payload.get("show_changelog_on_start")
    if isinstance(show_changelog_on_start, bool):
        settings["show_changelog_on_start"] = show_changelog_on_start

    window_role_bindings = payload.get("window_role_bindings")
    if isinstance(window_role_bindings, dict):
        settings["window_role_bindings"] = {
            title.strip(): role.strip()
            for title, role in window_role_bindings.items()
            if isinstance(title, str) and isinstance(role, str)
            and title.strip() and is_role_name_candidate(role.strip())
        }
    return settings


def save_user_settings(
    settings: dict[str, str | float | bool | dict[str, str]], settings_path: Path = SETTINGS_PATH,
) -> None:
    """Persist only simple preferences; OCR results and screen captures are not stored."""
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logging.warning("Unable to save settings to %s", settings_path, exc_info=True)


def load_changelog(changelog_path: Path = CHANGELOG_PATH) -> str:
    """Load the changelog bundled with the app, with a useful fallback for source runs."""
    try:
        return changelog_path.read_text(encoding="utf-8-sig")
    except OSError:
        return "未找到本地更新日志。请前往 GitHub Releases 查看版本记录。"


def render_changelog(markdown: str) -> str:
    """Render the bundled Markdown changelog as clean, readable plain text."""
    rendered: list[str] = []
    for raw_line in markdown.splitlines():
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw_line).replace("**", "").replace("`", "")
        if match := re.match(r"^#{1,3}\s+(.*)$", line):
            title = match.group(1).strip()
            rendered.append(title)
            if raw_line.startswith("## "):
                rendered.append("─" * min(48, max(12, len(title) * 2)))
        elif match := re.match(r"^\s*[-*+]\s+(.*)$", line):
            rendered.append(f"• {match.group(1).strip()}")
        else:
            rendered.append(line)
    return "\n".join(rendered).strip()


def parse_version(version: str) -> tuple[int, int, int] | None:
    """Parse release tags such as v1.2.0 without adding a dependency."""
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", version.strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    """Return whether a GitHub release tag is newer than the installed version."""
    candidate_version = parse_version(candidate)
    current_version = parse_version(current)
    return candidate_version is not None and current_version is not None and candidate_version > current_version


def fetch_latest_release(
    api_url: str = GITHUB_LATEST_RELEASE_API_URL,
) -> dict[str, str]:
    """Read the latest public GitHub Release metadata without downloading an executable."""
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "GuoLingZhuShou"})
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tag_name = str(payload.get("tag_name", "")).strip()
    html_url = str(payload.get("html_url", "")).strip()
    if not tag_name or not html_url:
        raise ValueError("GitHub Release 响应缺少版本信息")
    return {"tag_name": tag_name, "html_url": html_url}


def ocr_model_directories(model_root: Path = OCR_MODEL_DIR) -> dict[str, str]:
    """Keep PaddleOCR models outside PyInstaller's temporary extraction folder."""
    try:
        model_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logging.warning("Unable to create OCR model cache at %s", model_root, exc_info=True)
    return {
        "det_model_dir": str(model_root / "det"),
        "rec_model_dir": str(model_root / "rec"),
        "cls_model_dir": str(model_root / "cls"),
    }


def ensure_writable_error_stream() -> None:
    """Allow PaddleOCR download progress to run in a windowed EXE without a console."""
    if sys.stderr is None or not hasattr(sys.stderr, "write"):
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def find_qqsg_windows() -> list[tuple[int, str, tuple[int, int, int, int]]]:
    """返回可见 QQ 三国顶层窗口的句柄、标题与屏幕矩形。"""
    if sys.platform != "win32":
        return []

    user32 = ctypes.windll.user32
    windows: list[tuple[int, str, tuple[int, int, int, int]]] = []
    enum_callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_callback_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        title_length = user32.GetWindowTextLengthW(hwnd)
        if not title_length:
            return True
        buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, buffer, title_length + 1)
        title = buffer.value
        if "QQ三国" not in title:
            return True
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            windows.append((hwnd, title, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def capture_game_window(hwnd: int, rect: tuple[int, int, int, int]) -> Image.Image:
    """用 PrintWindow 直接截取目标窗口，不受其他窗口遮挡影响。"""
    try:
        import win32con
        import win32gui
        import win32ui

        current_rect = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(current_rect)):
            rect = (current_rect.left, current_rect.top, current_rect.right, current_rect.bottom)
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        window_dc_handle = win32gui.GetWindowDC(hwnd)
        source_dc = win32ui.CreateDCFromHandle(window_dc_handle)
        memory_dc = source_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        memory_dc.SelectObject(bitmap)
        copied = ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 2)
        if not copied:
            memory_dc.BitBlt((0, 0), (width, height), source_dc, (0, 0), win32con.SRCCOPY)
        image = Image.frombuffer("RGB", (width, height), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc_handle)
        return image
    except ModuleNotFoundError as error:
        raise RuntimeError("缺少 pywin32；请重新运行“安装国令助手依赖.cmd”") from error


def capture_game_window_wgc(
    hwnd: int, timeout_seconds: float = WGC_CAPTURE_TIMEOUT_SECONDS
) -> Image.Image:
    """Use Windows Graphics Capture to receive one target-window frame."""
    try:
        from windows_capture import WindowsCapture
    except ModuleNotFoundError as error:
        raise RuntimeError("windows-capture is not installed") from error

    completed = threading.Event()
    frame_image: list[Image.Image] = []
    capture_errors: list[str] = []
    capture = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

    @capture.event
    def on_frame_arrived(frame: object, control: object) -> None:
        try:
            buffer = frame.frame_buffer.copy()
            frame_image.append(Image.fromarray(buffer[:, :, :3][:, :, ::-1].copy(), "RGB"))
        except Exception as error:
            capture_errors.append(repr(error))
        finally:
            control.stop()
            completed.set()

    @capture.event
    def on_closed() -> None:
        capture_errors.append("WGC capture session closed")
        completed.set()

    session = capture.start_free_threaded()
    if not completed.wait(timeout_seconds):
        session.stop()
        raise RuntimeError(f"WGC timed out after {timeout_seconds:g} seconds")
    if frame_image:
        return frame_image[0]
    detail = "; ".join(capture_errors) or "no image frame received"
    raise RuntimeError(f"WGC window capture failed: {detail}")


def extract_candidate(lines: list[str]) -> str:
    """仅从完整的任务道具行提取物品，避免菜单文字被误当成任务物品。"""
    text = "\n".join(lines)
    for pattern in ITEM_PATTERNS:
        for match in re.finditer(pattern, text):
            candidate = match.group(1)
            if candidate not in EXCLUDED_WORDS:
                return candidate
    return ""


@lru_cache(maxsize=1)
def load_item_aliases() -> dict[str, str]:
    """读取确认过的 OCR 误读纠错表；格式错误时保持 OCR 原结果可用。"""
    try:
        data = json.loads(ITEM_ALIASES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(source): str(target) for source, target in data.items() if source and target}
    except (OSError, ValueError, TypeError):
        return {}


@lru_cache(maxsize=1)
def load_role_aliases() -> dict[str, str]:
    """Load user-confirmed OCR corrections for character names."""
    try:
        data = json.loads(ROLE_ALIASES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(source): str(target) for source, target in data.items() if source and target}
    except (OSError, ValueError, TypeError):
        return {}


def correct_role_name(candidate: str) -> str:
    """Apply explicit character-name corrections without guessing a new role."""
    normalized = re.sub(r"\s+", "", candidate)
    return load_role_aliases().get(normalized, normalized)


@lru_cache(maxsize=1)
def load_official_item_names() -> frozenset[str]:
    """读取随程序保存的 QQ 三国官网物品词表。"""
    try:
        data = json.loads(ITEM_VOCABULARY_PATH.read_text(encoding="utf-8-sig"))
        items = data.get("items", [])
        if not isinstance(items, list):
            return frozenset()
        return frozenset(str(item) for item in items if isinstance(item, str))
    except (OSError, ValueError, TypeError):
        return frozenset()


@lru_cache(maxsize=4)
def load_custom_item_vocabulary(
    vocabulary_path: Path = CUSTOM_ITEM_VOCABULARY_PATH,
) -> tuple[frozenset[str], dict[str, str]]:
    """Load user-maintained names and explicit OCR corrections."""
    try:
        data = json.loads(vocabulary_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return frozenset(), {}
        items = data.get("items", [])
        aliases = data.get("aliases", {})
        if not isinstance(items, list) or not isinstance(aliases, dict):
            return frozenset(), {}
        names = {str(item).strip() for item in items if str(item).strip()}
        normalized_aliases = {
            str(source).strip(): str(target).strip()
            for source, target in aliases.items()
            if str(source).strip() and str(target).strip()
        }
        names.update(normalized_aliases.values())
        return frozenset(names), normalized_aliases
    except (OSError, ValueError, TypeError):
        return frozenset(), {}


def save_custom_item_vocabulary(
    items: set[str] | frozenset[str] | list[str],
    aliases: dict[str, str],
    vocabulary_path: Path = CUSTOM_ITEM_VOCABULARY_PATH,
) -> None:
    """Save validated custom vocabulary and make it available immediately."""
    normalized_items = {str(item).strip() for item in items if str(item).strip()}
    normalized_aliases = {
        str(source).strip(): str(target).strip()
        for source, target in aliases.items()
        if str(source).strip() and str(target).strip()
    }
    normalized_items.update(normalized_aliases.values())
    payload = {
        "items": sorted(normalized_items),
        "aliases": dict(sorted(normalized_aliases.items())),
    }
    vocabulary_path.parent.mkdir(parents=True, exist_ok=True)
    vocabulary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    load_custom_item_vocabulary.cache_clear()


def load_all_item_names() -> frozenset[str]:
    """Combine official and user-provided names without altering bundled data."""
    custom_names, _aliases = load_custom_item_vocabulary()
    return load_official_item_names() | custom_names


def normalize_market_keyword(value: str) -> str:
    """Use half-width parentheses for all third-party market searches."""
    return value.strip().translate(str.maketrans({"（": "(", "）": ")"}))


def learn_market_item_name(
    keyword: str,
    rows: list[dict[str, str]],
    vocabulary_path: Path = CUSTOM_ITEM_VOCABULARY_PATH,
) -> str:
    """Save one unambiguous third-party market result for future OCR matching."""
    normalized_keyword = re.sub(r"\s+", "", normalize_market_keyword(keyword))
    names = {
        re.sub(r"\s+", "", normalize_market_keyword(str(row.get("name", ""))))
        for row in rows
        if str(row.get("name", "")).strip() not in {"", "-"}
    }
    if not normalized_keyword or len(names) != 1:
        return ""
    discovered_name = names.pop()
    custom_names, aliases = load_custom_item_vocabulary(vocabulary_path)
    existing_alias = aliases.get(normalized_keyword)
    if existing_alias and existing_alias != discovered_name:
        return ""
    if discovered_name in custom_names and (normalized_keyword == discovered_name or existing_alias == discovered_name):
        return discovered_name
    updated_names = set(custom_names)
    updated_names.add(discovered_name)
    if normalized_keyword != discovered_name:
        aliases[normalized_keyword] = discovered_name
    save_custom_item_vocabulary(updated_names, aliases, vocabulary_path)
    return discovered_name


@lru_cache(maxsize=1)
def load_official_monsters() -> tuple[dict[str, str], ...]:
    """读取随程序发布的官网怪物资料，用于离线查询。"""
    try:
        data = json.loads(MONSTER_VOCABULARY_PATH.read_text(encoding="utf-8-sig"))
        monsters = data.get("monsters", [])
        if not isinstance(monsters, list):
            return ()
        return tuple(
            {
                "name": str(monster.get("name", "")).strip(),
                "level": str(monster.get("level", "")).strip(),
                "location": str(monster.get("location", "")).strip(),
                "drops": str(monster.get("drops", "")).strip(),
            }
            for monster in monsters
            if isinstance(monster, dict) and str(monster.get("name", "")).strip()
        )
    except (OSError, ValueError, TypeError):
        return ()


@lru_cache(maxsize=1)
def load_official_npcs() -> tuple[dict[str, str], ...]:
    """Load the bundled official NPC directory for OCR correction."""
    try:
        data = json.loads(NPC_VOCABULARY_PATH.read_text(encoding="utf-8-sig"))
        npcs = data.get("npcs", [])
        if not isinstance(npcs, list):
            return ()
        return tuple(
            {
                "name": str(npc.get("name", "")).strip(),
                "location": str(npc.get("location", "")).strip(),
                "x": str(npc.get("x", "")).strip(),
                "y": str(npc.get("y", "")).strip(),
            }
            for npc in npcs
            if isinstance(npc, dict) and str(npc.get("name", "")).strip()
        )
    except (OSError, ValueError, TypeError):
        return ()


@lru_cache(maxsize=1)
def load_official_npc_names() -> frozenset[str]:
    return frozenset(npc["name"] for npc in load_official_npcs())


def search_official_monsters(
    query: str, monsters: tuple[dict[str, str], ...] | None = None
) -> tuple[dict[str, str], ...]:
    """按怪物名、等级、地点或掉落物筛选本地怪物词表。"""
    entries = load_official_monsters() if monsters is None else monsters
    normalized_query = re.sub(r"\s+", "", query).casefold()
    if not normalized_query:
        return entries

    matched = []
    for monster in entries:
        normalized_fields = {
            field: re.sub(r"\s+", "", monster.get(field, "")).casefold()
            for field in ("name", "level", "location", "drops")
        }
        if any(normalized_query in value for value in normalized_fields.values()):
            matched.append((normalized_fields, monster))

    # Exact monster names should appear first, followed by partial name matches.
    matched.sort(key=lambda item: (
        item[0]["name"] != normalized_query,
        normalized_query not in item[0]["name"],
        item[1]["name"],
        item[1]["level"],
        item[1]["location"],
    ))
    return tuple(monster for _fields, monster in matched)


def is_monster_task_target(lines: list[str], candidate: str = "", target_kind: str = "") -> bool:
    """Identify monster objectives while respecting an already parsed item target."""
    if target_kind == "item":
        return False
    if target_kind == "monster":
        return True
    text = _normalise_ocr_text("".join(lines))
    if not any(term in text for term in MONSTER_OBJECTIVE_TERMS):
        return False
    if "怪物" in text:
        return True
    normalized_candidate = re.sub(r"\s+", "", candidate).casefold()
    if not normalized_candidate:
        return False
    return any(
        normalized_candidate == re.sub(r"\s+", "", monster["name"]).casefold()
        for monster in load_official_monsters()
    )


def _edit_distance(left: str, right: str) -> int:
    """小词表中使用的字符编辑距离，避免引入额外运行时依赖。"""
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_character != right_character),
            ))
        previous = current
    return previous[-1]


def _match_unique_one_character_correction(candidate: str, names: frozenset[str]) -> str:
    if candidate in names or len(candidate) < 2:
        return candidate
    closest_names = [name for name in names if len(name) == len(candidate) and _edit_distance(name, candidate) == 1]
    return closest_names[0] if len(closest_names) == 1 else candidate


def correct_npc_name(candidate: str) -> str:
    """Correct a single OCR character only when the official NPC name is unique."""
    normalized = re.sub(r"\s+", "", candidate)
    return _match_unique_one_character_correction(normalized, load_official_npc_names())


ITEM_GRADE_PATTERN = re.compile(r"^(下品|中品|上品|极品|初级|中级|高级|特级|[一二三四五六七八九]阶)")


def _item_grade_prefix(name: str) -> str:
    match = ITEM_GRADE_PATTERN.match(name)
    return match.group(1) if match else ""


def match_official_item_name(candidate: str) -> str:
    """Correct an OCR item name only when one vocabulary result is clearly closest."""
    candidate = normalize_market_keyword(candidate)
    names = load_all_item_names()
    if candidate in names or len(candidate) < 3:
        return candidate

    # Item OCR may lose or misread two characters in longer names.  Keep the
    # correction bounded and unique, and never cross a quality/tier prefix.
    candidate_grade = _item_grade_prefix(candidate)
    maximum_distance = 1 if len(candidate) <= 5 else 2
    matches = [
        (_edit_distance(name, candidate), name)
        for name in names
        if abs(len(name) - len(candidate)) <= maximum_distance
        and (not candidate_grade or _item_grade_prefix(name) == candidate_grade)
    ]
    matches = [(distance, name) for distance, name in matches if distance <= maximum_distance]
    if not matches:
        return candidate
    closest_distance = min(distance for distance, _name in matches)
    closest_names = [name for distance, name in matches if distance == closest_distance]
    return closest_names[0] if len(closest_names) == 1 else candidate


def correct_item_name(candidate: str) -> str:
    _custom_names, custom_aliases = load_custom_item_vocabulary()
    aliases = {**load_item_aliases(), **custom_aliases}
    corrected = aliases.get(candidate, candidate)
    if corrected == candidate:
        for mistaken, expected in aliases.items():
            suffix = candidate.removeprefix(mistaken)
            if suffix != candidate and re.fullmatch(r"-\d+级", suffix):
                corrected = expected + suffix
                break
    return match_official_item_name(normalize_market_keyword(corrected))


def _entry_box(entry: tuple[list[list[float]], str]) -> tuple[float, float, float, float]:
    points, _text = entry
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


HUD_LEVEL_ONLY_PATTERN = re.compile(r"\s*[Ll1IiSs5][vVyY]?\s*\d{1,4}\s*$")
HUD_LEVEL_AND_NAME_PATTERN = re.compile(r"\s*[Ll1IiSs5][vVyY]?\s*\d{1,4}\s+(.+?)\s*$")


def find_hud_role_name(
    entries: list[tuple[list[list[float]], str]], allow_isolated_name: bool = False,
) -> str | None:
    """Prefer the name positioned beside the level in QQ SG's player HUD."""
    level_boxes: list[tuple[float, float, float, float]] = []
    candidates: list[tuple[tuple[float, float, float, float], str]] = []
    for entry in entries:
        box = _entry_box(entry)
        text = entry[1].strip()
        combined = HUD_LEVEL_AND_NAME_PATTERN.fullmatch(text)
        if combined:
            role = re.sub(r"\s+", "", combined.group(1))
            if is_role_name_candidate(role):
                return role
        if HUD_LEVEL_ONLY_PATTERN.fullmatch(text):
            level_boxes.append(box)
        role = re.sub(r"\s+", "", text)
        if is_role_name_candidate(role):
            candidates.append((box, role))

    nearby: list[tuple[float, str]] = []
    for role_box, role in candidates:
        left, top, _right, bottom = role_box
        center_y = (top + bottom) / 2
        for level_left, level_top, level_right, level_bottom in level_boxes:
            level_center_y = (level_top + level_bottom) / 2
            level_height = max(1, level_bottom - level_top)
            if left >= level_right - level_height and abs(center_y - level_center_y) <= level_height * 1.2:
                nearby.append((left - level_right, role))
    if nearby:
        return min(nearby, key=lambda item: item[0])[1]

    # Only use an isolated word after a dedicated crop around the level/name.
    # On the full HUD this fallback can mistake unrelated interface text for a role.
    if allow_isolated_name and candidates:
        return min(candidates, key=lambda item: (item[0][1], item[0][0]))[1]
    return None


def crop_player_name_from_level(
    image: Image.Image, entries: list[tuple[list[list[float]], str]]
) -> Image.Image | None:
    """Focus OCR on the text immediately to the right of a detected HUD level."""
    for entry in entries:
        if not HUD_LEVEL_ONLY_PATTERN.fullmatch(entry[1].strip()):
            continue
        left, top, right, bottom = _entry_box(entry)
        height = max(1, bottom - top)
        crop_box = (
            max(0, int(right - height * 0.4)),
            max(0, int(top - height * 1.1)),
            min(image.width, int(right + max(220, height * 14))),
            min(image.height, int(bottom + height * 1.6)),
        )
        if crop_box[2] - crop_box[0] >= 20 and crop_box[3] - crop_box[1] >= 20:
            return image.crop(crop_box)
    return None


def _normalise_ocr_text(text: str) -> str:
    return re.sub(r"[\s：:，,。．·_\-]", "", text)


def default_task_crop_box(image: Image.Image) -> tuple[int, int, int, int]:
    width, height = image.size
    left, top, right, bottom = TASK_CROP
    return int(width * left), int(height * top), int(width * right), int(height * bottom)


def cloud_task_crop_box(image: Image.Image) -> tuple[int, int, int, int]:
    """Include the complete right-side task card for a one-request cloud pass."""
    width, height = image.size
    left, top, right, bottom = CLOUD_TASK_CROP
    return int(width * left), int(height * top), int(width * right), int(height * bottom)


def player_info_crop_box(
    image: Image.Image, client_box: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    """Return the upper-left HUD area inside the game client, not its title bar."""
    width, height = image.size
    if client_box is None:
        client_box = (0, 0, width, height)
    client_left, client_top, client_right, client_bottom = client_box
    region_left, region_top, region_right, region_bottom = PLAYER_NAME_REGION
    client_width = client_right - client_left
    client_height = client_bottom - client_top
    return (
        max(0, client_left + round(client_width * region_left)),
        max(0, client_top + round(client_height * region_top)),
        min(width, client_left + round(client_width * region_right)),
        min(height, client_top + round(client_height * region_bottom)),
    )


def game_client_box(hwnd: int | None, image: Image.Image) -> tuple[int, int, int, int]:
    """Map the Win32 client area onto a captured window image when it has a frame."""
    width, height = image.size
    whole_image = (0, 0, width, height)
    if hwnd is None or sys.platform != "win32":
        return whole_image
    try:
        window_rect = wintypes.RECT()
        client_rect = wintypes.RECT()
        client_origin = wintypes.POINT(0, 0)
        user32 = ctypes.windll.user32
        if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            return whole_image
        if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
            return whole_image
        if not user32.ClientToScreen(hwnd, ctypes.byref(client_origin)):
            return whole_image
        window_width = window_rect.right - window_rect.left
        window_height = window_rect.bottom - window_rect.top
        client_width = client_rect.right - client_rect.left
        client_height = client_rect.bottom - client_rect.top
        if min(window_width, window_height, client_width, client_height) <= 0:
            return whole_image

        # Windows Graphics Capture may already return client pixels only.
        if abs(width - client_width) <= 3 and abs(height - client_height) <= 3:
            return whole_image

        scale_x = width / window_width
        scale_y = height / window_height
        left = round((client_origin.x - window_rect.left) * scale_x)
        top = round((client_origin.y - window_rect.top) * scale_y)
        right = left + round(client_width * scale_x)
        bottom = top + round(client_height * scale_y)
        if left < 0 or top < 0 or right > width or bottom > height or right <= left or bottom <= top:
            return whole_image
        return left, top, right, bottom
    except (AttributeError, OSError):
        return whole_image


def player_ocr_target_size(image_size: tuple[int, int]) -> tuple[int, int]:
    """Enlarge the compact player HUD without a costly full-window OCR pass."""
    width, height = image_size
    scale = min(PLAYER_OCR_SCALE, math.sqrt(PLAYER_OCR_MAX_PIXELS / (width * height)))
    return max(1, round(width * scale)), max(1, round(height * scale))


def has_task_panel_context(lines: list[str]) -> bool:
    """Avoid accepting a crop containing only player names or scene labels."""
    text = _normalise_ocr_text("".join(lines))
    return any(term in text for term in TASK_CONTEXT_TERMS)


def locate_task_panel(
    image: Image.Image, entries: list[tuple[list[list[float]], str]]
) -> tuple[Image.Image, str, tuple[int, int, int, int]]:
    """按任务相关文字定位面板；仅在无法定位时使用旧比例作为回退。"""
    width, height = image.size
    anchors: list[tuple[int, float, tuple[list[list[float]], str], str]] = []
    keywords = (
        ("任务追踪", 500),
        ("国令慕贤", 450),
        ("高级国令", 400),
        ("国令", 350),
        ("任务", 150),
    )
    # Paddle 有时把“任务追踪”拆成相邻的两段，因此加入同一行相邻文本的组合。
    candidates = entries.copy()
    for first_index, first in enumerate(entries):
        first_left, first_top, first_right, first_bottom = _entry_box(first)
        first_height = max(1, first_bottom - first_top)
        for second in entries[first_index + 1:]:
            second_left, second_top, second_right, second_bottom = _entry_box(second)
            same_line = abs((first_top + first_bottom) - (second_top + second_bottom)) / 2 < first_height
            nearby = -first_height <= second_left - first_right <= width * 0.08
            if same_line and nearby:
                candidates.append((
                    [[min(first_left, second_left), min(first_top, second_top)],
                     [max(first_right, second_right), min(first_top, second_top)],
                     [max(first_right, second_right), max(first_bottom, second_bottom)],
                     [min(first_left, second_left), max(first_bottom, second_bottom)]],
                    first[1] + second[1],
                ))

    for entry in candidates:
        left, top, right, bottom = _entry_box(entry)
        center_x = (left + right) / 2
        # Task tracking is placed on the right side.  World labels in the middle
        # may occasionally be read as a task keyword, so do not use them as anchors.
        if center_x < width * 0.72:
            continue
        text = _normalise_ocr_text(entry[1])
        for keyword, priority in keywords:
            if keyword in text:
                anchors.append((priority, center_x, entry, keyword))
                break

    if not anchors:
        crop_box = default_task_crop_box(image)
        return image.crop(crop_box), "未找到任务关键词，使用默认区域", crop_box

    _priority, _center_x, entry, keyword = max(anchors, key=lambda anchor: (anchor[0], anchor[1]))
    left, top, right, bottom = _entry_box(entry)
    anchor_height = max(1, bottom - top)
    anchor_width = max(1, right - left)

    # 标题在任务面板最上方；任务名称命中时则同时覆盖其上方标题与下方要求。
    if keyword == "任务追踪":
        crop_left = left - width * 0.035
        crop_top = top - max(height * 0.018, anchor_height * 0.8)
        crop_right = max(right + width * 0.20, left + width * 0.25)
        crop_bottom = bottom + max(height * 0.23, anchor_height * 8)
    else:
        crop_left = left - max(width * 0.10, anchor_width * 1.5)
        crop_top = top - max(height * 0.12, anchor_height * 4.5)
        crop_right = right + max(width * 0.15, anchor_width * 3)
        crop_bottom = bottom + max(height * 0.17, anchor_height * 6)

    crop_box = (
        max(0, int(crop_left)),
        max(0, int(crop_top)),
        min(width, int(crop_right)),
        min(height, int(crop_bottom)),
    )
    if crop_box[2] - crop_box[0] < 30 or crop_box[3] - crop_box[1] < 30:
        crop_box = default_task_crop_box(image)
        return image.crop(crop_box), "任务关键词定位区域无效，使用默认区域", crop_box
    return image.crop(crop_box), f"关键词定位：{keyword}", crop_box


class CropOverlay(tk.Toplevel):
    def __init__(self, parent: tk.Tk, image: Image.Image, callback: callable) -> None:
        super().__init__(parent)
        self.callback = callback
        self.image = image
        self.start: tuple[int, int] | None = None
        self.rectangle: int | None = None
        self.photo = ImageTk.PhotoImage(image)
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.canvas = tk.Canvas(self, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._finish)
        self.bind("<Escape>", lambda _event: self.destroy())

    def _start(self, event: tk.Event) -> None:
        self.start = (event.x, event.y)
        if self.rectangle:
            self.canvas.delete(self.rectangle)
        self.rectangle = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#ffcc00", width=3)

    def _drag(self, event: tk.Event) -> None:
        if self.start and self.rectangle:
            self.canvas.coords(self.rectangle, self.start[0], self.start[1], event.x, event.y)

    def _finish(self, event: tk.Event) -> None:
        if not self.start:
            return
        left, right = sorted((self.start[0], event.x))
        top, bottom = sorted((self.start[1], event.y))
        if right - left < 10 or bottom - top < 10:
            return
        self.destroy()
        self.callback(self.image.crop((left, top, right, bottom)))


class HotkeyRecorder(tk.Toplevel):
    """Small modal recorder for a single key or a modifier-key chord."""

    _modifier_names = {
        "Control_L": "ctrl",
        "Control_R": "ctrl",
        "Alt_L": "alt",
        "Alt_R": "alt",
        "Shift_L": "shift",
        "Shift_R": "shift",
        "Win_L": "windows",
        "Win_R": "windows",
    }
    _key_aliases = {
        "Return": "enter",
        "Escape": "esc",
        "BackSpace": "backspace",
        "space": "space",
        "Prior": "page up",
        "Next": "page down",
        "Print": "print screen",
        "Pause": "pause",
        "Insert": "insert",
        "Delete": "delete",
        "Home": "home",
        "End": "end",
        "Left": "left",
        "Right": "right",
        "Up": "up",
        "Down": "down",
        "plus": "plus",
        "minus": "minus",
    }

    def __init__(self, parent: tk.Tk, callback: callable, cancel_callback: callable) -> None:
        super().__init__(parent)
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._pressed_modifiers: set[str] = set()
        self.title("录制快捷键")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(bg="#edf1f5")
        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="按下要使用的按键或组合键", style="Section.TLabel").pack(anchor="w")
        ttk.Label(body, text="例如：F8、Ctrl+G、Ctrl+Shift+G", style="Note.TLabel").pack(anchor="w", pady=(5, 16))
        ttk.Button(body, text="取消", command=self._cancel).pack(anchor="e")
        self.bind("<KeyPress>", self._record)
        self.bind("<KeyRelease>", self._release)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(100, self.focus_force)

    def _record(self, event: tk.Event) -> str:
        modifier = self._modifier_names.get(event.keysym)
        if modifier:
            self._pressed_modifiers.add(modifier)
            return "break"
        hotkey = self._format_hotkey(event.keysym, self._pressed_modifiers)
        if not hotkey:
            return "break"
        self.grab_release()
        self.destroy()
        self._callback(hotkey)
        return "break"

    def _release(self, event: tk.Event) -> str | None:
        modifier = self._modifier_names.get(event.keysym)
        if modifier:
            self._pressed_modifiers.discard(modifier)
            return "break"
        return None

    @classmethod
    def _format_hotkey(cls, keysym: str, modifiers: set[str] | None = None) -> str:
        key = cls._key_aliases.get(keysym, keysym.lower())
        if not key or key in {"caps_lock", "num_lock", "scroll_lock"}:
            return ""
        modifier_order = ("ctrl", "alt", "shift", "windows")
        active_modifiers = modifiers or set()
        return "+".join((*[name for name in modifier_order if name in active_modifiers], key))

    def _cancel(self) -> None:
        self.grab_release()
        self.destroy()
        self._cancel_callback()


class MonsterLookupDialog(tk.Toplevel):
    """Offline lookup for the monster records bundled with the application."""

    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("怪物词表查询")
        self.geometry("940x500")
        self.minsize(720, 360)
        self.transient(parent)
        self.query_var = tk.StringVar()
        self.result_var = tk.StringVar()
        self._rows: dict[str, dict[str, str]] = {}

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        search_bar = ttk.Frame(body)
        search_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        search_bar.columnconfigure(1, weight=1)
        ttk.Label(search_bar, text="查询", style="Field.TLabel").grid(row=0, column=0, padx=(0, 8))
        search_entry = ttk.Entry(search_bar, textvariable=self.query_var, style="Result.TEntry")
        search_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(search_bar, text="清除", command=self._clear).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(search_bar, text="复制名称", command=self._copy_selected, style="Primary.TButton").grid(
            row=0, column=3, padx=(8, 0)
        )

        table_holder = ttk.Frame(body)
        table_holder.grid(row=1, column=0, sticky="nsew")
        table_holder.columnconfigure(0, weight=1)
        table_holder.rowconfigure(0, weight=1)
        columns = ("name", "level", "location", "drops")
        self.table = ttk.Treeview(table_holder, columns=columns, show="headings", selectmode="browse")
        self.table.heading("name", text="怪物名称")
        self.table.heading("level", text="等级")
        self.table.heading("location", text="出没地点")
        self.table.heading("drops", text="掉落物品")
        self.table.column("name", width=170, minwidth=120, anchor="w", stretch=False)
        self.table.column("level", width=70, minwidth=55, anchor="center", stretch=False)
        self.table.column("location", width=180, minwidth=130, anchor="w", stretch=False)
        self.table.column("drops", width=470, minwidth=180, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical_scroll = ttk.Scrollbar(table_holder, orient="vertical", command=self.table.yview)
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll = ttk.Scrollbar(table_holder, orient="horizontal", command=self.table.xview)
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)

        ttk.Label(body, textvariable=self.result_var, style="Note.TLabel").grid(row=2, column=0, sticky="w", pady=(9, 0))
        self.query_var.trace_add("write", self._refresh)
        self.table.bind("<Double-1>", lambda _event: self._copy_selected())
        self._refresh()
        self.after(100, search_entry.focus_set)

    def _refresh(self, *_args: object) -> None:
        for item_id in self.table.get_children():
            self.table.delete(item_id)
        self._rows.clear()
        matches = search_official_monsters(self.query_var.get())
        for index, monster in enumerate(matches):
            item_id = str(index)
            self._rows[item_id] = monster
            self.table.insert(
                "",
                "end",
                iid=item_id,
                values=(monster["name"], monster["level"], monster["location"], monster["drops"] or "-"),
            )
        self.result_var.set(f"找到 {len(matches)} 条怪物资料。可按怪物名、等级、地点或掉落物筛选。")

    def _clear(self) -> None:
        self.query_var.set("")

    def _copy_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一条怪物资料。", parent=self)
            return
        monster = self._rows[selected[0]]
        self.clipboard_clear()
        self.clipboard_append(monster["name"])
        self.update()
        self.result_var.set(f"已复制“{monster['name']}”。")


class CustomVocabularyDialog(tk.Toplevel):
    """Editor for user-provided item names and explicit OCR corrections."""

    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.title("自定义词库")
        self.geometry("760x510")
        self.minsize(620, 390)
        self.transient(parent)
        custom_names, custom_aliases = load_custom_item_vocabulary()
        self.items = set(custom_names)
        self.aliases = custom_aliases.copy()
        self.name_var = tk.StringVar()
        self.alias_var = tk.StringVar()
        self.status_var = tk.StringVar()

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text="自定义任务物品词库", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text="只填写正确名称即可参与单字符 OCR 纠错；识别差异较大时，再填写对应的 OCR 误识名称。",
            style="Note.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        editor = ttk.Frame(body, style="Surface.TFrame", padding=12)
        editor.grid(row=2, column=0, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        editor.rowconfigure(2, weight=1)
        ttk.Label(editor, text="正确名称", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        name_entry = ttk.Entry(editor, textvariable=self.name_var, style="Result.TEntry")
        name_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(editor, text="OCR 误识名称（可选）", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
        )
        alias_entry = ttk.Entry(editor, textvariable=self.alias_var)
        alias_entry.grid(row=1, column=1, sticky="ew", pady=(8, 0))

        actions = ttk.Frame(editor, style="Surface.TFrame")
        actions.grid(row=0, column=2, rowspan=2, sticky="ns", padx=(10, 0))
        ttk.Button(actions, text="添加 / 更新", command=self._add_or_update, style="Primary.TButton").pack(fill="x")
        ttk.Button(actions, text="删除误识词", command=self._delete_alias).pack(fill="x", pady=(7, 0))
        ttk.Button(actions, text="清空输入", command=self._clear_inputs).pack(fill="x", pady=(7, 0))

        table_holder = ttk.Frame(editor, style="Surface.TFrame")
        table_holder.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        table_holder.columnconfigure(0, weight=1)
        table_holder.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_holder, columns=("name", "aliases"), show="headings", selectmode="browse")
        self.table.heading("name", text="正确名称")
        self.table.heading("aliases", text="已设置的 OCR 误识名称")
        self.table.column("name", width=210, minwidth=140, anchor="w", stretch=False)
        self.table.column("aliases", width=440, minwidth=220, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_holder, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)

        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(footer, text="删除所选名称", command=self._delete_selected).pack(side="right")
        ttk.Label(footer, textvariable=self.status_var, style="Note.TLabel").pack(side="left")
        self.table.bind("<<TreeviewSelect>>", self._select_item)
        self._refresh_table()
        self.after(100, name_entry.focus_set)

    def _refresh_table(self) -> None:
        for item_id in self.table.get_children():
            self.table.delete(item_id)
        for name in sorted(self.items):
            aliases = sorted(source for source, target in self.aliases.items() if target == name)
            self.table.insert("", "end", iid=name, values=(name, "、".join(aliases) or "-"))
        self.status_var.set(f"已保存 {len(self.items)} 个自定义名称、{len(self.aliases)} 条纠错规则。")

    def _save(self) -> bool:
        try:
            save_custom_item_vocabulary(self.items, self.aliases)
        except OSError as error:
            messagebox.showerror("保存失败", f"无法保存自定义词库：{error}", parent=self)
            return False
        self._refresh_table()
        return True

    def _add_or_update(self) -> None:
        name = self.name_var.get().strip()
        alias = self.alias_var.get().strip()
        if not name:
            messagebox.showinfo("提示", "请填写正确名称。", parent=self)
            return
        self.items.add(name)
        if alias and alias != name:
            self.aliases[alias] = name
        elif alias == name:
            self.aliases.pop(alias, None)
        if self._save():
            self._clear_inputs()
            self.status_var.set(f"已添加“{name}”，OCR 识别将立即使用该词库。")

    def _delete_alias(self) -> None:
        alias = self.alias_var.get().strip()
        if not alias:
            messagebox.showinfo("提示", "请先填写要删除的 OCR 误识名称。", parent=self)
            return
        if alias not in self.aliases:
            messagebox.showinfo("提示", f"未找到“{alias}”对应的纠错规则。", parent=self)
            return
        del self.aliases[alias]
        if self._save():
            self.alias_var.set("")
            self.status_var.set(f"已删除“{alias}”的纠错规则。")

    def _delete_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要删除的自定义名称。", parent=self)
            return
        name = selected[0]
        self.items.discard(name)
        self.aliases = {source: target for source, target in self.aliases.items() if target != name}
        if self._save():
            self._clear_inputs()
            self.status_var.set(f"已删除“{name}”及其纠错规则。")

    def _select_item(self, _event: tk.Event) -> None:
        selected = self.table.selection()
        if not selected:
            return
        name = selected[0]
        aliases = sorted(source for source, target in self.aliases.items() if target == name)
        self.name_var.set(name)
        self.alias_var.set(aliases[0] if aliases else "")

    def _clear_inputs(self) -> None:
        self.name_var.set("")
        self.alias_var.set("")
        for item_id in self.table.selection():
            self.table.selection_remove(item_id)


class TaskProgressDialog(tk.Toplevel):
    """Show locally saved multi-step task progress for each game role."""

    def __init__(self, parent: "GuolingTaskOcr") -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("任务步数追踪")
        self.geometry("1080x530")
        self.minsize(880, 430)
        self.transient(parent)
        self.row_keys: dict[str, tuple[str, str, int, int]] = {}
        self.task_filter_var = tk.StringVar(value="全部")
        self.round_filter_var = tk.StringVar(value="全部")
        self.date_filter_var = tk.StringVar(value="全部")
        self.manual_round_var = tk.StringVar()
        self.manual_step_var = tk.StringVar()

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        controls = ttk.Frame(body, style="Surface.TFrame", padding=10)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)
        controls.columnconfigure(5, weight=1)
        ttk.Label(controls, text="当前角色", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        role_entry = ttk.Entry(controls, textvariable=parent.task_role_var)
        role_entry.grid(row=0, column=1, sticky="ew")
        role_entry.bind("<FocusOut>", self._save_role)
        role_entry.bind("<Return>", self._save_role)
        ttk.Button(controls, text="使用当前 OCR", command=self._record_current_ocr).grid(row=0, column=2, padx=(8, 0))
        self.round_summary_var = tk.StringVar()
        ttk.Label(controls, text="各轮成本", style="Field.TLabel").grid(row=1, column=0, sticky="nw", pady=(8, 0), padx=(0, 8))
        ttk.Label(controls, textvariable=self.round_summary_var, style="Note.TLabel", justify="left", wraplength=940).grid(
            row=1, column=1, columnspan=6, sticky="w", pady=(8, 0)
        )
        ttk.Label(controls, text="任务类型", style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0), padx=(0, 8))
        self.task_filter_combo = ttk.Combobox(controls, textvariable=self.task_filter_var, state="readonly")
        self.task_filter_combo.grid(row=2, column=1, sticky="ew", pady=(10, 0))
        ttk.Label(controls, text="轮次", style="Field.TLabel").grid(row=2, column=2, sticky="w", pady=(10, 0), padx=(14, 8))
        self.round_filter_combo = ttk.Combobox(controls, textvariable=self.round_filter_var, state="readonly", width=10)
        self.round_filter_combo.grid(row=2, column=3, sticky="ew", pady=(10, 0))
        ttk.Label(controls, text="日期", style="Field.TLabel").grid(row=2, column=4, sticky="w", pady=(10, 0), padx=(14, 8))
        self.date_filter_combo = ttk.Combobox(controls, textvariable=self.date_filter_var, state="readonly", width=13)
        self.date_filter_combo.grid(row=2, column=5, sticky="ew", pady=(10, 0))
        ttk.Button(controls, text="重置筛选", command=self._reset_filters).grid(row=2, column=6, padx=(8, 0), pady=(10, 0))
        for combo in (self.task_filter_combo, self.round_filter_combo, self.date_filter_combo):
            combo.bind("<<ComboboxSelected>>", self._filter_changed)

        table_holder = ttk.Frame(body, style="Surface.TFrame", padding=1)
        table_holder.grid(row=1, column=0, sticky="nsew")
        table_holder.rowconfigure(0, weight=1)
        table_holder.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(
            table_holder,
            columns=("role", "task", "round", "progress", "objective", "quantity", "cost", "updated"),
            show="headings",
            selectmode="browse",
        )
        for column, label, width in (
            ("role", "角色", 120),
            ("task", "任务", 150),
            ("round", "轮次", 60),
            ("progress", "当前步骤", 100),
            ("objective", "任务目标", 170),
            ("quantity", "需求", 70),
            ("cost", "行情成本", 170),
            ("updated", "最后识别", 140),
        ):
            self.table.heading(column, text=label)
            self.table.column(column, width=width, minwidth=90, anchor="w", stretch=column == "task")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_holder, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<<TreeviewSelect>>", self._select_record)

        footer = ttk.Frame(body)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=parent.task_progress_status_var, style="Note.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="删除所选", command=self._delete_selected).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(footer, text="清空记录", command=self._clear_records).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(footer, text="关闭", command=self.close).grid(row=0, column=3, padx=(8, 0))
        editor = ttk.Frame(footer)
        editor.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(editor, text="修改所选记录：轮次", style="Field.TLabel").pack(side="left")
        ttk.Spinbox(editor, from_=1, to=9999, width=6, textvariable=self.manual_round_var).pack(side="left", padx=(6, 14))
        ttk.Label(editor, text="步骤", style="Field.TLabel").pack(side="left")
        ttk.Spinbox(editor, from_=1, to=9999, width=6, textvariable=self.manual_step_var).pack(side="left", padx=(6, 10))
        ttk.Button(editor, text="应用修改", command=self._apply_manual_position).pack(side="left")
        self._refresh_table()
        self.after(100, role_entry.focus_set)

    def _save_role(self, _event: tk.Event | None = None) -> str:
        self.parent_app.task_role_var.set(self.parent_app.task_role_var.get().strip())
        self.parent_app._save_settings()
        self._refresh_table()
        return "break"

    def _record_current_ocr(self) -> None:
        lines = self.parent_app.raw_text.get("1.0", "end").splitlines()
        self.parent_app._record_task_progress(lines)
        self._refresh_table()

    def _selected_record(self) -> tuple[tuple[str, str, int, int], TaskProgress] | None:
        selected = self.table.selection()
        if not selected:
            return None
        key = self.row_keys.get(selected[0])
        record = self.parent_app.task_records.get(key) if key else None
        return (key, record) if key and record else None

    def _select_record(self, _event: tk.Event | None = None) -> None:
        selected = self._selected_record()
        if selected is None:
            return
        _key, record = selected
        self.manual_round_var.set(str(record.round_index))
        self.manual_step_var.set(str(record.current_step))

    def _apply_manual_position(self) -> None:
        selected = self._selected_record()
        if selected is None:
            messagebox.showinfo("提示", "请先选择要修改的任务记录。", parent=self)
            return
        key, record = selected
        try:
            round_index = int(self.manual_round_var.get())
            current_step = int(self.manual_step_var.get())
            updated = update_task_progress_position(
                self.parent_app.task_records, key, round_index, current_step
            )
        except ValueError as error:
            if str(error) != "目标轮次和步骤已有记录。":
                messagebox.showerror("修改失败", str(error), parent=self)
                return
            if not messagebox.askyesno(
                "覆盖记录",
                f"第 {round_index} 轮第 {current_step} 步已有记录，是否用当前“{record.display_objective}”覆盖？",
                parent=self,
            ):
                return
            updated = update_task_progress_position(
                self.parent_app.task_records, key, round_index, current_step, overwrite=True
            )
        except KeyError as error:
            messagebox.showerror("修改失败", str(error), parent=self)
            return
        self.parent_app._save_task_records()
        self.parent_app.task_progress_status_var.set(
            f"已手动调整：{updated.role} · {updated.task} · 第 {updated.round_index} 轮 · {updated.display_progress}。"
        )
        self._refresh_table()

    def _filter_changed(self, _event: tk.Event | None = None) -> None:
        self._refresh_table()

    def _reset_filters(self) -> None:
        self.task_filter_var.set("全部")
        self.round_filter_var.set("全部")
        self.date_filter_var.set("全部")
        self._refresh_table()

    @staticmethod
    def _select_filter_value(variable: tk.StringVar, values: tuple[str, ...]) -> None:
        if variable.get() not in values:
            variable.set("全部")

    def _refresh_filter_values(self) -> None:
        records = list(self.parent_app.task_records.values())
        task_values = ("全部", *sorted({record.task for record in records}))
        round_values = ("全部", *(f"第 {value} 轮" for value in sorted({record.round_index for record in records})))
        date_values = ("全部", *sorted({record.updated_at[:10] for record in records if len(record.updated_at) >= 10}, reverse=True))
        self.task_filter_combo.configure(values=task_values)
        self.round_filter_combo.configure(values=round_values)
        self.date_filter_combo.configure(values=date_values)
        self._select_filter_value(self.task_filter_var, task_values)
        self._select_filter_value(self.round_filter_var, round_values)
        self._select_filter_value(self.date_filter_var, date_values)

    def _delete_selected(self) -> None:
        selected = self.table.selection()
        if not selected:
            return
        key = self.row_keys.get(selected[0])
        if key:
            self.parent_app.task_records.pop(key, None)
            self.parent_app._save_task_records()
            self.parent_app.task_progress_status_var.set("已删除所选步数记录。")
        self._refresh_table()

    def _clear_records(self) -> None:
        if not self.parent_app.task_records:
            return
        if not messagebox.askyesno("清空记录", "确定清空所有角色的任务步数记录吗？", parent=self):
            return
        self.parent_app.task_records.clear()
        self.parent_app._save_task_records()
        self.parent_app.task_progress_status_var.set("已清空任务步数记录。")
        self._refresh_table()

    def _refresh_table(self) -> None:
        for item_id in self.table.get_children():
            self.table.delete(item_id)
        self.row_keys.clear()
        selected_role = self.parent_app.task_role_var.get().strip()
        self.round_summary_var.set(self.parent_app.task_round_summary_text(selected_role))
        self._refresh_filter_values()
        round_text = self.round_filter_var.get()
        round_match = re.fullmatch(r"第\s*(\d+)\s*轮", round_text)
        selected_round = int(round_match.group(1)) if round_match else None
        selected_task = self.task_filter_var.get() if self.task_filter_var.get() != "全部" else ""
        selected_date = self.date_filter_var.get() if self.date_filter_var.get() != "全部" else ""
        records = filter_task_progress_records(
            self.parent_app.task_records,
            role=selected_role,
            task=selected_task,
            round_index=selected_round,
            date=selected_date,
        )
        records.sort(
            key=lambda record: (
                record.role,
                record.task,
                record.round_index,
                record.current_step,
                record.updated_at,
            )
        )
        for index, record in enumerate(records):
            item_id = str(index)
            self.row_keys[item_id] = task_record_key(record)
            self.table.insert(
                "", "end", iid=item_id,
                values=(
                    record.role, record.task, f"第 {record.round_index} 轮", record.display_progress, record.display_objective,
                    record.display_quantity, record.display_cost, record.updated_at,
                ),
            )
        if not records:
            self.parent_app.task_progress_status_var.set("识别到任务进度后会自动记录在这里。")

    def close(self) -> None:
        self.parent_app._save_settings()
        self.destroy()


class AboutDialog(tk.Toplevel):
    """Show the installed version, bundled changelog, and GitHub release status."""

    def __init__(self, parent: tk.Tk) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("关于与更新")
        self.geometry("760x560")
        self.minsize(620, 420)
        self.transient(parent)
        self.release_url = GITHUB_RELEASES_URL
        self.update_status_var = tk.StringVar(value="可检查 GitHub 上的最新 Release。")

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text="国令助手", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=f"当前版本：v{__version__}", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 10))

        changelog_holder = ttk.Frame(body, style="Surface.TFrame", padding=1)
        changelog_holder.grid(row=2, column=0, sticky="nsew")
        changelog_holder.rowconfigure(0, weight=1)
        changelog_holder.columnconfigure(0, weight=1)
        changelog_text = tk.Text(
            changelog_holder,
            wrap="word",
            relief="flat",
            borderwidth=0,
            background="#f8fafc",
            foreground="#27364a",
            font=("Microsoft YaHei UI", 9),
            padx=12,
            pady=10,
        )
        changelog_text.grid(row=0, column=0, sticky="nsew")
        changelog_scrollbar = ttk.Scrollbar(changelog_holder, orient="vertical", command=changelog_text.yview)
        changelog_scrollbar.grid(row=0, column=1, sticky="ns")
        changelog_text.configure(yscrollcommand=changelog_scrollbar.set)
        changelog_text.insert("1.0", render_changelog(load_changelog()))
        changelog_text.configure(state="disabled")

        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.update_status_var, style="Note.TLabel").grid(row=0, column=0, sticky="w")
        self.hide_on_start_var = tk.BooleanVar(value=not parent.show_changelog_on_start_var.get())
        ttk.Checkbutton(
            footer,
            text="启动时不再显示",
            variable=self.hide_on_start_var,
            command=self._save_changelog_preference,
        ).grid(row=0, column=1, padx=(8, 0))
        self.check_button = ttk.Button(footer, text="检查更新", command=self._check_for_updates)
        self.check_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(footer, text="打开 Release 页面", command=self._open_release_page).grid(row=0, column=3, padx=(8, 0))

    def _save_changelog_preference(self) -> None:
        self.parent_app.show_changelog_on_start_var.set(not self.hide_on_start_var.get())
        self.parent_app._save_settings()

    def _check_for_updates(self) -> None:
        self.check_button.configure(state="disabled")
        self.update_status_var.set("正在检查 GitHub Release……")
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self) -> None:
        try:
            release = fetch_latest_release()
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
            self.after(0, lambda message=str(error): self._show_update_error(message))
            return
        self.after(0, lambda: self._show_update_result(release))

    def _show_update_error(self, message: str) -> None:
        self.check_button.configure(state="normal")
        self.update_status_var.set(f"检查更新失败：{message}。")

    def _show_update_result(self, release: dict[str, str]) -> None:
        self.check_button.configure(state="normal")
        self.release_url = release["html_url"]
        latest = release["tag_name"]
        if is_newer_version(latest):
            self.update_status_var.set(f"发现新版本 {latest}。请在 Release 页面下载完整 EXE。")
        elif is_newer_version(__version__, latest):
            self.update_status_var.set(f"当前版本 v{__version__} 高于 GitHub Release {latest}，可能尚未发布。")
        else:
            self.update_status_var.set(f"当前已是最新版本（GitHub 最新：{latest}）。")

    def _open_release_page(self) -> None:
        webbrowser.open_new_tab(self.release_url)


class FlashAlertDialog(tk.Toplevel):
    """Configure and run an in-process alert for Windows taskbar flash events."""

    SOUND_LABELS = {
        "系统提示音": "system",
        "WAV 文件": "wav",
        "蜂鸣音": "beep",
    }

    def __init__(self, parent: "GuolingTaskOcr") -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("窗口闪烁提醒")
        self.geometry("960x650")
        self.minsize(800, 530)
        self.transient(parent)
        self.window_handles: dict[str, int] = {}
        self.reports: list[str] = []
        self.status_var = parent.flash_status_var
        self.sound_label_var = tk.StringVar(value=self._sound_label(parent.flash_sound_mode_var.get()))

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)
        ttk.Label(body, text="窗口闪烁提醒", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text="当目标窗口在任务栏闪烁时播放声音。可选择指定窗口或按自定义标题关键词监控。",
            style="Note.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        target_settings = ttk.Frame(body, style="Surface.TFrame", padding=12)
        target_settings.grid(row=2, column=0, sticky="ew")
        target_settings.columnconfigure(1, weight=1)
        ttk.Label(target_settings, text="监听目标", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 9))
        mode_holder = ttk.Frame(target_settings, style="Toolbar.TFrame")
        mode_holder.grid(row=1, column=1, sticky="w")
        ttk.Label(target_settings, text="监控方式", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Radiobutton(
            mode_holder, text="指定窗口", variable=parent.flash_target_mode_var, value="window", command=self._target_mode_changed
        ).pack(side="left")
        ttk.Radiobutton(
            mode_holder, text="自定义关键词", variable=parent.flash_target_mode_var, value="keyword", command=self._target_mode_changed
        ).pack(side="left", padx=(12, 0))
        ttk.Label(target_settings, text="标题关键词", style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=(9, 0), padx=(0, 10))
        self.filter_entry = ttk.Entry(target_settings, textvariable=parent.flash_title_filter_var)
        self.filter_entry.grid(row=2, column=1, sticky="ew", pady=(9, 0))
        self.filter_entry.bind("<FocusOut>", self._save_preferences)
        ttk.Label(target_settings, text="指定窗口", style="Field.TLabel").grid(row=3, column=0, sticky="w", pady=(9, 0), padx=(0, 10))
        self.window_combo = ttk.Combobox(
            target_settings, textvariable=parent.flash_window_var, state="readonly"
        )
        self.window_combo.grid(row=3, column=1, sticky="ew", pady=(9, 0))
        self.window_combo.bind("<<ComboboxSelected>>", self._save_preferences)
        target_actions = ttk.Frame(target_settings, style="Toolbar.TFrame")
        target_actions.grid(row=4, column=1, sticky="e", pady=(9, 0))
        ttk.Button(target_actions, text="刷新窗口列表", command=self.refresh_windows).pack(side="left")
        ttk.Button(target_actions, text="使用当前游戏窗口", command=self.use_current_game_window).pack(side="left", padx=(7, 0))

        alert_settings = ttk.Frame(body, style="Surface.TFrame", padding=12)
        alert_settings.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        alert_settings.columnconfigure(1, weight=1)
        ttk.Label(alert_settings, text="提醒方式", style="Section.TLabel").grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 9))
        ttk.Label(alert_settings, text="提醒声音", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10))
        sound_combo = ttk.Combobox(
            alert_settings, textvariable=self.sound_label_var, values=tuple(self.SOUND_LABELS), state="readonly", width=14
        )
        sound_combo.grid(row=1, column=1, sticky="w")
        sound_combo.bind("<<ComboboxSelected>>", self._sound_changed)
        ttk.Label(alert_settings, text="冷却时间", style="Field.TLabel").grid(row=1, column=2, sticky="e", padx=(18, 5))
        cooldown = ttk.Spinbox(
            alert_settings,
            from_=MIN_FLASH_COOLDOWN_SECONDS,
            to=MAX_FLASH_COOLDOWN_SECONDS,
            increment=0.5,
            textvariable=parent.flash_cooldown_var,
            width=5,
            justify="center",
        )
        cooldown.grid(row=1, column=3, sticky="w")
        cooldown.bind("<FocusOut>", self._save_preferences)
        cooldown.bind("<Return>", self._save_preferences)
        ttk.Label(alert_settings, text="秒", style="Field.TLabel").grid(row=1, column=4, sticky="w", padx=(5, 0))

        ttk.Label(alert_settings, text="WAV 路径", style="Field.TLabel").grid(row=2, column=0, sticky="w", pady=(9, 0), padx=(0, 10))
        wav_entry = ttk.Entry(alert_settings, textvariable=parent.flash_wav_path_var)
        wav_entry.grid(row=2, column=1, columnspan=3, sticky="ew", pady=(9, 0))
        wav_entry.bind("<FocusOut>", self._save_preferences)
        ttk.Button(alert_settings, text="选择 WAV 文件", command=self.choose_wav).grid(row=2, column=4, padx=(8, 0), pady=(9, 0))

        report_holder = ttk.Frame(body, style="Surface.TFrame", padding=12)
        report_holder.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        report_holder.rowconfigure(1, weight=1)
        report_holder.columnconfigure(0, weight=1)
        ttk.Label(report_holder, text="检测报告", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(report_holder, text="清空", command=self.clear_reports).grid(row=0, column=1, sticky="e")
        self.report_text = tk.Text(
            report_holder,
            height=9,
            wrap="word",
            relief="solid",
            borderwidth=1,
            background="#f8fafc",
            foreground="#27364a",
            font=("Microsoft YaHei UI", 9),
            state="disabled",
            padx=8,
            pady=7,
        )
        self.report_text.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(7, 0))

        footer = ttk.Frame(body)
        footer.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(1, weight=1)
        ttk.Button(footer, text="测试声音", command=self.test_sound).grid(row=0, column=0)
        ttk.Label(footer, textvariable=self.status_var, style="Note.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_windows()
        self._update_target_mode_widgets()

    @classmethod
    def _sound_label(cls, mode: str) -> str:
        return next((label for label, value in cls.SOUND_LABELS.items() if value == mode), "系统提示音")

    def _add_report(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.reports.append(f"{timestamp}  {message}")
        self.reports = self.reports[-100:]
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        self.report_text.insert("1.0", "\n".join(self.reports))
        self.report_text.see("end")
        self.report_text.configure(state="disabled")

    def clear_reports(self) -> None:
        self.reports.clear()
        self._add_report("已清空检测报告")

    def refresh_windows(self) -> None:
        self.parent_app.refresh_flash_windows()
        self.window_handles = self.parent_app.flash_window_handles
        values = tuple(self.window_handles)
        self.window_combo.configure(values=values)
        self._add_report(f"刷新窗口列表：找到 {len(values)} 个可见窗口")

    def use_current_game_window(self) -> None:
        selection = self.parent_app.game_windows.get(self.parent_app.window_var.get())
        if not selection:
            messagebox.showinfo("提示", "请先在主界面选择 QQ 三国窗口。", parent=self)
            return
        hwnd, _rect = selection
        label = next((candidate for candidate, candidate_hwnd in self.window_handles.items() if candidate_hwnd == hwnd), "")
        if not label:
            self.refresh_windows()
            label = next((candidate for candidate, candidate_hwnd in self.window_handles.items() if candidate_hwnd == hwnd), "")
        if not label:
            messagebox.showinfo("提示", "当前游戏窗口不在可监听窗口列表中。", parent=self)
            return
        self.parent_app.flash_window_var.set(label)
        self._save_preferences()
        self._add_report("已将当前 QQ 三国窗口设为提醒目标")

    def _sound_changed(self, _event: tk.Event | None = None) -> None:
        self.parent_app.flash_sound_mode_var.set(self.SOUND_LABELS[self.sound_label_var.get()])
        self._save_preferences()

    def _save_preferences(self, _event: tk.Event | None = None) -> None:
        self.parent_app._save_settings()

    def _target_mode_changed(self) -> None:
        self._update_target_mode_widgets()
        self._save_preferences()

    def _update_target_mode_widgets(self) -> None:
        keyword_mode = self.parent_app.flash_target_mode_var.get() == "keyword"
        self.filter_entry.configure(state="normal" if keyword_mode else "disabled")
        self.window_combo.configure(state="disabled" if keyword_mode else "readonly")

    def choose_wav(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择提醒 WAV 文件",
            filetypes=(("WAV 音频", "*.wav"), ("所有文件", "*.*")),
        )
        if path:
            self.parent_app.flash_wav_path_var.set(path)
            self._save_preferences()

    def _cooldown_seconds(self) -> float:
        try:
            value = float(self.parent_app.flash_cooldown_var.get())
        except (TypeError, ValueError):
            value = DEFAULT_FLASH_COOLDOWN_SECONDS
        value = min(MAX_FLASH_COOLDOWN_SECONDS, max(MIN_FLASH_COOLDOWN_SECONDS, value))
        self.parent_app.flash_cooldown_var.set(f"{value:g}")
        return value

    def test_sound(self) -> None:
        success = play_sound(self.parent_app.flash_sound_mode_var.get(), self.parent_app.flash_wav_path_var.get())
        self.status_var.set("已播放测试声音" if success else "测试声音播放失败")
        self._add_report("测试声音播放成功" if success else "测试声音播放失败")

    def close(self) -> None:
        self.parent_app._save_settings()
        self.destroy()


class CloudOcrSettingsDialog(tk.Toplevel):
    """Configure the optional cloud OCR service without embedding credentials."""

    def __init__(self, parent: "GuolingTaskOcr") -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("云端 OCR 设置")
        self.geometry("720x440")
        self.minsize(620, 400)
        self.transient(parent)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        mode = ttk.Frame(body, style="Surface.TFrame", padding=12)
        mode.grid(row=0, column=0, sticky="ew")
        ttk.Label(mode, text="识别方式", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Radiobutton(mode, text="本地 PaddleOCR（默认）", value="local", variable=parent.ocr_mode_var, command=self._save).grid(
            row=1, column=0, sticky="w"
        )
        ttk.Radiobutton(mode, text="云端 PaddleOCR API", value="cloud", variable=parent.ocr_mode_var, command=self._save).grid(
            row=1, column=1, sticky="w", padx=(28, 0)
        )
        ttk.Label(
            mode,
            text="云端模式会上传待识别的游戏截图；启用后，后续识别不会加载本地 PaddleOCR 模型。",
            style="Note.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(9, 0))

        credentials = ttk.Frame(body, style="Surface.TFrame", padding=12)
        credentials.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        credentials.columnconfigure(1, weight=1)
        ttk.Label(credentials, text="云端 API", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(credentials, text="API 令牌", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10))
        token_entry = ttk.Entry(credentials, textvariable=parent.cloud_ocr_token_var, show="*")
        token_entry.grid(row=1, column=1, sticky="ew")
        ttk.Label(credentials, text="模型", style="Field.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(9, 0))
        ttk.Entry(credentials, textvariable=parent.cloud_ocr_model_var).grid(row=2, column=1, sticky="ew", pady=(9, 0))
        ttk.Label(credentials, text="新版 API 地址", style="Field.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=(9, 0))
        ttk.Entry(credentials, textvariable=parent.cloud_ocr_api_url_var).grid(row=3, column=1, sticky="ew", pady=(9, 0))
        ttk.Label(
            credentials,
            text="填写官方文档生成的新版 API 地址时使用同步接口；留空则使用 PP-OCRv6 兼容接口。",
            style="Note.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(9, 0))
        ttk.Label(
            credentials,
            text="令牌仅保存到本机设置文件，且不会写入日志或项目文件。请勿分享或提交令牌。",
            style="Note.TLabel",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        footer = ttk.Frame(body)
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer,
            text="若本地模型已加载，切换到云端后重启助手可立即释放其内存占用。",
            style="Note.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="保存", command=self.close, style="Primary.TButton").grid(row=0, column=1)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, token_entry.focus_set)

    def _save(self) -> None:
        self.parent_app.cloud_ocr_token_var.set(self.parent_app.cloud_ocr_token_var.get().strip())
        self.parent_app.cloud_ocr_model_var.set(self.parent_app.cloud_ocr_model_var.get().strip() or DEFAULT_CLOUD_OCR_MODEL)
        self.parent_app.cloud_ocr_api_url_var.set(self.parent_app.cloud_ocr_api_url_var.get().strip())
        self.parent_app._save_settings()

    def close(self) -> None:
        self._save()
        self.destroy()


class MarketSettingsDialog(tk.Toplevel):
    """Configure the user-authorized account used by the main-window market panel."""

    REGIONS = ("得陇", "三足", "暗渡", "巧借", "群雄", "一代", "单刀", "杜康", "桃园", "抚琴", "十八", "云骑", "青梅")

    def __init__(self, parent: "GuolingTaskOcr") -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.title("行情查询设置")
        self.geometry("760x350")
        self.minsize(640, 310)
        self.transient(parent)
        self.account_var = tk.StringVar(value=parent.market_session.account if parent.market_session else parent.market_account_var.get())
        self.password_var = tk.StringVar()
        self.status_var = tk.StringVar(value="登录后可在主界面直接查询摊位和商行行情。")

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        account = ttk.Frame(body, style="Surface.TFrame", padding=12)
        account.grid(row=0, column=0, sticky="ew")
        account.columnconfigure(1, weight=1)
        account.columnconfigure(3, weight=1)
        ttk.Label(account, text="账号授权", style="Section.TLabel").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 9))
        ttk.Label(account, text="账号", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(account, textvariable=self.account_var).grid(row=1, column=1, sticky="ew")
        ttk.Label(account, text="密码", style="Field.TLabel").grid(row=1, column=2, sticky="e", padx=(14, 8))
        password_entry = ttk.Entry(account, textvariable=self.password_var, show="*")
        password_entry.grid(row=1, column=3, sticky="ew")
        self.login_button = ttk.Button(account, text="登录", command=self._login, style="Primary.TButton")
        self.login_button.grid(row=1, column=4, padx=(10, 0))
        self.logout_button = ttk.Button(account, text="退出登录", command=self._logout)
        self.logout_button.grid(row=1, column=5, padx=(7, 0))
        ttk.Label(account, text="密码只用于本次登录，不会保存到本机。", style="Note.TLabel").grid(
            row=2, column=0, columnspan=6, sticky="w", pady=(8, 0)
        )

        preferences = ttk.Frame(body, style="Surface.TFrame", padding=12)
        preferences.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(preferences, text="查询偏好", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 9))
        ttk.Label(preferences, text="默认区服", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10))
        region_combo = ttk.Combobox(
            preferences, textvariable=parent.market_region_var, values=self.REGIONS, state="readonly", width=10
        )
        region_combo.grid(row=1, column=1, sticky="w")
        region_combo.bind("<<ComboboxSelected>>", parent._save_settings)

        footer = ttk.Frame(body)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Note.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="打开原网站", command=lambda: webbrowser.open(MARKET_WEB_URL)).grid(row=0, column=1)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, password_entry.focus_set)

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        state = "disabled" if busy else "normal"
        self.login_button.configure(state=state)
        self.logout_button.configure(state=state)
        if message:
            self.status_var.set(message)

    def _login(self) -> None:
        account = self.account_var.get().strip()
        password = self.password_var.get()
        if not account or not password:
            self.status_var.set("请输入账号和密码。")
            return
        self._set_busy(True, "正在登录...")

        def worker() -> None:
            try:
                session = self.parent_app.market_client.login(account, password)
            except MarketQueryError as error:
                self.after(0, self._login_done, None, str(error))
            else:
                self.after(0, self._login_done, session, "")

        threading.Thread(target=worker, name="market-login", daemon=True).start()

    def _login_done(self, session: MarketSession | None, error: str) -> None:
        if not self.winfo_exists():
            return
        self._set_busy(False)
        if session is None:
            self.status_var.set(f"登录失败：{error}")
            return
        self.password_var.set("")
        self.parent_app.set_market_session(session)
        self.account_var.set(session.account)
        self.status_var.set(f"已登录“{session.account}”。")
        self.parent_app.market_status_var.set("登录成功，可在主界面查询行情。")

    def _logout(self) -> None:
        self.password_var.set("")
        self.parent_app.clear_market_session()
        self.status_var.set("已退出登录，并清除本机保存的登录令牌。")
        self.parent_app.market_status_var.set("请在行情设置中登录后再查询。")

    def close(self) -> None:
        self.parent_app._save_settings()
        self.destroy()


class GuolingTaskOcr(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("国令助手")
        self.geometry("1240x980")
        self.minsize(1020, 760)
        self.configure(bg="#edf1f5")
        self.source_image: Image.Image | None = None
        self.crop_image: Image.Image | None = None
        self.locate_from_source = False
        self.source_window_handle: int | None = None
        self.cached_task_region: tuple[float, float, float, float] | None = None
        self.cached_task_window: int | None = None
        self.last_task_signature: bytes | None = None
        self.last_detected_role = ""
        self.last_valid_item = ""
        self.last_ocr_is_monster_target = False
        self.last_ocr_target_kind = ""
        self.preview_image: ImageTk.PhotoImage | None = None
        self.ocr_engine = None
        self.ocr_lock = threading.Lock()
        self.cloud_ocr_client = CloudOcrClient()
        self.ocr_in_progress = False
        self.role_check_in_progress = False
        self.hotkey_id = None
        settings = load_user_settings()
        self.auto_var = tk.BooleanVar(value=False)
        self.interval_var = tk.StringVar(value=f"{float(settings['interval_seconds']):g}")
        self.hotkey_var = tk.StringVar(value=str(settings["hotkey"]))
        self.flash_title_filter_var = tk.StringVar(value=str(settings["flash_title_filter"]))
        self.flash_target_mode_var = tk.StringVar(value=str(settings["flash_target_mode"]))
        self.flash_window_var = tk.StringVar(value=str(settings["flash_window_title"]))
        self.flash_sound_mode_var = tk.StringVar(value=str(settings["flash_sound_mode"]))
        self.flash_wav_path_var = tk.StringVar(value=str(settings["flash_wav_path"]))
        self.flash_cooldown_var = tk.StringVar(value=f"{float(settings['flash_cooldown_seconds']):g}")
        self.flash_enabled_var = tk.BooleanVar(value=bool(settings["flash_enabled"]))
        saved_role = str(settings["task_tracker_role"]).strip()
        self.task_role_var = tk.StringVar(value=saved_role if is_role_name_candidate(saved_role) else "")
        self.show_changelog_on_start_var = tk.BooleanVar(value=bool(settings["show_changelog_on_start"]))
        self.task_records: dict[tuple[str, str, int, int], TaskProgress] = load_task_progress(TASK_PROGRESS_PATH)
        self.last_task_record_key: tuple[str, str, int, int] | None = None
        saved_bindings = settings["window_role_bindings"]
        self.window_role_bindings: dict[str, str] = dict(saved_bindings) if isinstance(saved_bindings, dict) else {}
        self.role_binding_var = tk.StringVar()
        self.task_progress_status_var = tk.StringVar(value="识别到任务进度后会自动记录。")
        self.market_account_var = tk.StringVar(value=str(settings["market_account"]))
        self.market_region_var = tk.StringVar(value=str(settings["market_region"]))
        market_token = str(settings["market_token"]).strip()
        market_user_id = str(settings["market_user_id"]).strip()
        self.market_session = (
            MarketSession(self.market_account_var.get(), market_token, market_user_id)
            if market_token and market_user_id
            else None
        )
        self.market_client = MarketClient()
        self.market_item_var = tk.StringVar()
        self.market_auto_query_var = tk.BooleanVar(value=bool(settings["market_auto_query"]))
        self.ocr_mode_var = tk.StringVar(value=str(settings["ocr_mode"]))
        self.cloud_ocr_token_var = tk.StringVar(value=str(settings["cloud_ocr_token"]))
        self.cloud_ocr_model_var = tk.StringVar(value=str(settings["cloud_ocr_model"]))
        self.cloud_ocr_api_url_var = tk.StringVar(value=str(settings["cloud_ocr_api_url"]))
        self.market_status_var = tk.StringVar(value="请在“行情设置”中登录后查询摊位和商行信息。")
        self.market_detail_var = tk.StringVar()
        self.market_rows: dict[str, dict[str, str]] = {}
        self.market_last_keyword = ""
        self.market_busy = False
        self.market_last_auto_query: tuple[str, str] | None = None
        self.flash_events: Queue[FlashEvent] = Queue(maxsize=FLASH_EVENT_QUEUE_SIZE)
        self.flash_monitor = FlashMonitor(self.flash_events)
        self.flash_window_handles: dict[str, int] = {}
        self.flash_last_alert: dict[int, float] = {}
        self.flash_status_var = tk.StringVar(value="闪烁提醒已关闭")
        self._auto_generation = 0
        self.window_var = tk.StringVar()
        self.capture_method_var = tk.StringVar(value=str(settings["capture_method"]))
        self.preferred_window_title = str(settings["window_title"])
        self.game_windows: dict[str, tuple[int, tuple[int, int, int, int]]] = {}
        self._style()
        self._build()
        self._register_hotkey()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(200, self.refresh_game_windows)
        self.after(250, self.refresh_flash_windows)
        self.after(300, self._restore_flash_monitor)
        self.after(100, self._process_flash_events)
        self.after(450, self._show_changelog_on_start)

    def _style_legacy(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TLabel", background="#f6f7f9", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"), foreground="#1e3a5f")
        style.configure("Note.TLabel", foreground="#667085", font=("Microsoft YaHei UI", 8))
        style.configure("Panel.TLabelframe", background="#f6f7f9")
        style.configure("Panel.TLabelframe.Label", background="#f6f7f9", foreground="#1e3a5f", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 7))

    def _build_legacy(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="国令助手", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="完整截取指定游戏窗口后，以“任务追踪 / 国令慕贤”等文字定位任务区域并识别物品。", style="Note.TLabel").pack(anchor="w", pady=(2, 12))

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="截取屏幕并框选", command=self.capture_and_select, style="Primary.TButton").pack(side="left")
        ttk.Button(actions, text="载入截图", command=self.load_image).pack(side="left", padx=8)
        ttk.Button(actions, text="使用默认区域（回退）", command=self.default_crop).pack(side="left")
        ttk.Button(actions, text="快捷识别  Ctrl+Alt+G", command=self.quick_capture_and_recognize).pack(side="left", padx=8)
        ttk.Checkbutton(actions, text="实时识别（每 2 秒）", variable=self.auto_var, command=self.toggle_auto).pack(side="left")
        self.recognize_button = ttk.Button(actions, text="识别并复制", command=self.recognize, state="disabled", style="Primary.TButton")
        self.recognize_button.pack(side="right")

        window_bar = ttk.Frame(root)
        window_bar.pack(fill="x", pady=(0, 10))
        ttk.Label(window_bar, text="QQ 三国窗口").pack(side="left")
        self.window_combo = ttk.Combobox(window_bar, textvariable=self.window_var, state="readonly", width=35)
        self.window_combo.pack(side="left", padx=(8, 6))
        ttk.Button(window_bar, text="刷新窗口", command=self.refresh_game_windows).pack(side="left")
        ttk.Button(window_bar, text="截图选中窗口", command=self.capture_selected_window, style="Primary.TButton").pack(side="left", padx=8)

        ttk.Label(window_bar, text="截图方式").pack(side="left", padx=(12, 0))
        self.capture_method_combo = ttk.Combobox(
            window_bar,
            textvariable=self.capture_method_var,
            values=("WGC（后台，高效）", "PrintWindow（兼容）"),
            state="readonly",
            width=19,
        )
        self.capture_method_combo.pack(side="left", padx=(8, 6))

        content = ttk.Frame(root)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        preview = ttk.LabelFrame(content, text="待识别区域", style="Panel.TLabelframe", padding=8)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.preview_label = ttk.Label(preview, text="尚未选择图片区域", anchor="center")
        self.preview_label.pack(fill="both", expand=True)

        result = ttk.LabelFrame(content, text="识别结果", style="Panel.TLabelframe", padding=8)
        result.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ttk.Label(result, text="任务物品名（可手动修正）").pack(anchor="w")
        self.item_var = tk.StringVar()
        item_row = ttk.Frame(result)
        item_row.pack(fill="x", pady=(4, 10))
        ttk.Entry(item_row, textvariable=self.item_var, font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", fill="x", expand=True)
        ttk.Button(item_row, text="复制", command=self.copy_item).pack(side="left", padx=(8, 0))
        ttk.Label(result, text="OCR 原始文本").pack(anchor="w")
        self.raw_text = tk.Text(result, height=17, font=("Microsoft YaHei UI", 10), wrap="word")
        self.raw_text.pack(fill="both", expand=True, pady=(4, 0))

        self.status_var = tk.StringVar(value="热键 Ctrl+Alt+G：完整截取选定窗口，自动定位任务并识别复制。")
        ttk.Label(root, textvariable=self.status_var, style="Note.TLabel").pack(anchor="w", pady=(10, 0))

    def _style(self) -> None:
        """Compact desktop-tool styling with a clear primary action hierarchy."""
        style = ttk.Style(self)
        style.theme_use("clam")
        font = "Microsoft YaHei UI"
        background = "#edf1f5"
        surface = "#ffffff"
        ink = "#18233a"
        muted = "#5f6f85"
        accent = "#0f766e"
        accent_hover = "#0b5e58"

        style.configure("TFrame", background=background)
        style.configure("Header.TFrame", background="#16233d")
        style.configure("Surface.TFrame", background=surface, borderwidth=1, relief="solid")
        style.configure("Toolbar.TFrame", background=surface)
        style.configure("TLabel", background=background, foreground=ink, font=(font, 9))
        style.configure("HeaderTitle.TLabel", background="#16233d", foreground="#ffffff", font=(font, 19, "bold"))
        style.configure("HeaderNote.TLabel", background="#16233d", foreground="#c7d4e7", font=(font, 9))
        style.configure("Section.TLabel", background=surface, foreground=ink, font=(font, 11, "bold"))
        style.configure("Field.TLabel", background=surface, foreground=muted, font=(font, 9))
        style.configure("Note.TLabel", background=background, foreground=muted, font=(font, 9))
        style.configure("Status.TLabel", background="#e3f4f1", foreground="#075c56", font=(font, 9))
        style.configure("TButton", font=(font, 9), padding=(10, 6), foreground=ink, background="#f8fafc")
        style.map("TButton", background=[("active", "#e7edf3")])
        style.configure("Primary.TButton", font=(font, 10, "bold"), padding=(14, 7), foreground="#ffffff", background=accent)
        style.map("Primary.TButton", background=[("active", accent_hover), ("disabled", "#9aa9b7")], foreground=[("disabled", "#eef4f7")])
        style.configure("Quick.TButton", font=(font, 9, "bold"), padding=(11, 6), foreground="#ffffff", background="#355d8c")
        style.map("Quick.TButton", background=[("active", "#294c73")])
        style.configure("TCheckbutton", background=surface, foreground=ink, font=(font, 9))
        style.map("TCheckbutton", background=[("active", surface)])
        style.configure("TCombobox", padding=(7, 5), font=(font, 9))
        style.configure("Result.TEntry", font=(font, 14, "bold"), padding=(10, 8))
        style.configure("Market.TFrame", background="#2f1a15", borderwidth=1, relief="solid")
        style.configure("MarketToolbar.TFrame", background="#2f1a15")
        style.configure("MarketTitle.TLabel", background="#2f1a15", foreground="#ffd65a", font=(font, 12, "bold"))
        style.configure("MarketField.TLabel", background="#2f1a15", foreground="#f7d9a5", font=(font, 9))
        style.configure("MarketNote.TLabel", background="#2f1a15", foreground="#d8bda2", font=(font, 9))
        style.configure("Market.TButton", font=(font, 9, "bold"), padding=(10, 6), foreground="#fff0bc", background="#71402b")
        style.map("Market.TButton", background=[("active", "#8a5134"), ("disabled", "#5c4b43")])
        style.configure("Market.TCheckbutton", background="#2f1a15", foreground="#f7d9a5", font=(font, 9))
        style.map("Market.TCheckbutton", background=[("active", "#2f1a15")])
        style.configure("Market.Treeview", background="#3e2119", fieldbackground="#3e2119", foreground="#fff2d1", rowheight=29, font=(font, 9), borderwidth=0)
        style.map("Market.Treeview", background=[("selected", "#765039")], foreground=[("selected", "#fff7d6")])
        style.configure("Market.Treeview.Heading", background="#5a2d1c", foreground="#ffe16d", font=(font, 9, "bold"), relief="flat", padding=(8, 6))
        style.map("Market.Treeview.Heading", background=[("active", "#754126")])

    def _build(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        header = ttk.Frame(root, style="Header.TFrame", padding=(18, 14))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="国令慕贤", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="窗口截图 · 自动定位 · OCR 识别",
            style="HeaderNote.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        source = ttk.Frame(root, style="Surface.TFrame", padding=(14, 11))
        source.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        source.columnconfigure(1, weight=1, minsize=250)
        source.columnconfigure(3, weight=1, minsize=220)
        ttk.Label(source, text="游戏窗口", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.window_combo = ttk.Combobox(source, textvariable=self.window_var, state="readonly", width=46)
        self.window_combo.grid(row=0, column=1, columnspan=3, sticky="ew")
        ttk.Button(source, text="刷新", command=self.refresh_game_windows).grid(row=0, column=4, padx=(8, 8))
        ttk.Button(source, text="截取窗口", command=self.capture_selected_window, style="Primary.TButton").grid(row=0, column=5, padx=(4, 0))
        ttk.Label(source, text="截图方式", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        self.capture_method_combo = ttk.Combobox(
            source,
            textvariable=self.capture_method_var,
            values=("WGC（后台，高效）", "PrintWindow（兼容）"),
            state="readonly",
            width=17,
        )
        self.capture_method_combo.grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.capture_method_combo.bind("<<ComboboxSelected>>", self._save_settings)
        self.window_combo.bind("<<ComboboxSelected>>", self._on_game_window_selected)
        ttk.Label(source, text="绑定角色", style="Field.TLabel").grid(
            row=1, column=2, sticky="w", pady=(8, 0), padx=(16, 8)
        )
        self.task_role_entry = ttk.Entry(source, textvariable=self.task_role_var)
        self.task_role_entry.grid(row=1, column=3, sticky="ew", pady=(8, 0))
        self.task_role_entry.bind("<FocusOut>", self._save_task_role)
        self.task_role_entry.bind("<Return>", self._save_task_role)
        ttk.Button(source, text="绑定窗口", command=self.bind_selected_window_role).grid(
            row=1, column=4, padx=(8, 8), pady=(8, 0)
        )
        ttk.Button(source, text="解除绑定", command=self.unbind_selected_window_role).grid(
            row=1, column=5, pady=(8, 0)
        )

        tools = ttk.Frame(root, style="Surface.TFrame", padding=(14, 9))
        tools.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        tools.columnconfigure(0, weight=1)

        capture_tools = ttk.Frame(tools, style="Toolbar.TFrame")
        capture_tools.grid(row=0, column=0, sticky="w")
        ttk.Button(capture_tools, text="框选屏幕", command=self.capture_and_select, width=10).pack(side="left")
        ttk.Button(capture_tools, text="载入截图", command=self.load_image, width=10).pack(side="left", padx=(7, 0))
        ttk.Button(capture_tools, text="默认区域", command=self.default_crop, width=10).pack(side="left", padx=(7, 0))
        utility_button = ttk.Menubutton(capture_tools, text="工具与设置")
        utility_button.pack(side="left", padx=(12, 0))
        utility_menu = tk.Menu(utility_button, tearoff=False)
        utility_menu.add_command(label="录制快捷键", command=self.record_hotkey)
        utility_menu.add_separator()
        utility_menu.add_command(label="怪物词表", command=self.open_monster_lookup)
        utility_menu.add_command(label="自定义词库", command=self.open_custom_vocabulary)
        utility_menu.add_command(label="云端 OCR 设置", command=self.open_cloud_ocr_settings)
        utility_menu.add_command(label="行情设置", command=self.open_market_settings)
        utility_menu.add_checkbutton(
            label="闪烁提醒", variable=self.flash_enabled_var, command=self.toggle_flash_monitor,
            onvalue=True, offvalue=False,
        )
        utility_menu.add_command(label="提醒设置", command=self.open_flash_alert)
        utility_menu.add_separator()
        utility_menu.add_command(label="关于 / 更新", command=self.open_about_dialog)
        utility_button.configure(menu=utility_menu)
        self.utility_menu = utility_menu

        right_tools = ttk.Frame(tools, style="Toolbar.TFrame")
        right_tools.grid(row=0, column=1, sticky="e")

        auto_tools = ttk.Frame(right_tools, style="Toolbar.TFrame")
        auto_tools.pack(side="left")
        ttk.Checkbutton(auto_tools, text="实时识别", variable=self.auto_var, command=self.toggle_auto).pack(side="left")
        ttk.Label(auto_tools, text="间隔", style="Field.TLabel").pack(side="left", padx=(12, 5))
        self.interval_spinbox = ttk.Spinbox(
            auto_tools,
            from_=MIN_AUTO_INTERVAL_SECONDS,
            to=MAX_AUTO_INTERVAL_SECONDS,
            increment=0.5,
            textvariable=self.interval_var,
            width=5,
            justify="center",
        )
        self.interval_spinbox.pack(side="left")
        self.interval_spinbox.bind("<FocusOut>", self._validate_auto_interval)
        self.interval_spinbox.bind("<Return>", self._validate_auto_interval)
        ttk.Label(auto_tools, text="秒", style="Field.TLabel").pack(side="left", padx=(5, 0))

        recognition_actions = ttk.Frame(right_tools, style="Toolbar.TFrame")
        recognition_actions.pack(side="left", padx=(18, 0))
        ttk.Button(recognition_actions, text="快捷识别", command=self.quick_capture_and_recognize, style="Quick.TButton").pack(side="left")
        self.recognize_button = ttk.Button(
            recognition_actions, text="识别并复制", command=self.recognize, state="disabled", style="Primary.TButton"
        )
        self.recognize_button.pack(side="left", padx=(8, 0))

        workspace = ttk.Notebook(root)
        workspace.grid(row=3, column=0, sticky="nsew")
        task_tab = ttk.Frame(workspace)
        market_tab = ttk.Frame(workspace)
        workspace.add(task_tab, text="任务识别")
        workspace.add(market_tab, text="物品行情")
        task_tab.columnconfigure(0, weight=1)
        task_tab.rowconfigure(0, weight=1)
        market_tab.columnconfigure(0, weight=1)
        market_tab.rowconfigure(0, weight=1)

        content = ttk.Frame(task_tab)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=4)
        content.columnconfigure(1, weight=5)
        content.rowconfigure(0, weight=1)

        preview = ttk.Frame(content, style="Surface.TFrame", padding=12)
        preview.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        ttk.Label(preview, text="任务截图", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.preview_label = ttk.Label(
            preview,
            text="截图后将在这里显示任务区域",
            style="Field.TLabel",
            anchor="center",
        )
        self.preview_label.grid(row=1, column=0, sticky="nsew")

        result = ttk.Frame(content, style="Surface.TFrame", padding=12)
        result.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        result.rowconfigure(5, weight=1)
        result.columnconfigure(0, weight=1)
        ttk.Label(result, text="识别结果", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Button(result, text="步数追踪", command=self.open_task_progress_dialog).grid(row=0, column=1, sticky="e", pady=(0, 10))
        summary = ttk.Frame(result, style="Surface.TFrame", padding=(8, 6))
        summary.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        summary.columnconfigure(1, weight=1)
        summary.columnconfigure(3, weight=1)
        self.recognized_role_var = tk.StringVar(value="未识别")
        self.target_name_var = tk.StringVar(value="未识别")
        self.target_quantity_var = tk.StringVar(value="-")
        self.npc_name_var = tk.StringVar(value="-")
        ttk.Label(summary, text="角色", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.recognized_role_var, style="Field.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 18))
        ttk.Label(summary, text="任务目标", style="Field.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(summary, textvariable=self.target_name_var, style="Field.TLabel").grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(summary, text="需求数量", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(summary, textvariable=self.target_quantity_var, style="Field.TLabel").grid(row=1, column=1, sticky="w", padx=(8, 18), pady=(5, 0))
        ttk.Label(summary, text="任务 NPC", style="Field.TLabel").grid(row=1, column=2, sticky="w", pady=(5, 0))
        ttk.Label(summary, textvariable=self.npc_name_var, style="Field.TLabel").grid(row=1, column=3, sticky="w", padx=(8, 0), pady=(5, 0))
        ttk.Label(result, text="任务目标", style="Field.TLabel").grid(row=2, column=0, sticky="w")
        self.item_var = tk.StringVar()
        self.item_entry = ttk.Entry(result, textvariable=self.item_var, style="Result.TEntry")
        self.item_entry.grid(row=3, column=0, sticky="ew", pady=(4, 13))
        ttk.Button(result, text="复制", command=self.copy_item, style="Primary.TButton").grid(row=3, column=1, padx=(8, 0), pady=(4, 13))
        ttk.Label(result, text="OCR 原始文本", style="Field.TLabel").grid(row=4, column=0, columnspan=2, sticky="nw")
        raw_holder = ttk.Frame(result, style="Surface.TFrame")
        raw_holder.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        raw_holder.rowconfigure(0, weight=1)
        raw_holder.columnconfigure(0, weight=1)
        self.raw_text = tk.Text(
            raw_holder,
            height=5,
            font=("Microsoft YaHei UI", 10),
            wrap="word",
            relief="solid",
            borderwidth=1,
            background="#f8fafc",
            foreground="#27364a",
            insertbackground="#27364a",
            padx=9,
            pady=8,
        )
        self.raw_text.grid(row=0, column=0, sticky="nsew")
        raw_scroll = ttk.Scrollbar(raw_holder, orient="vertical", command=self.raw_text.yview)
        raw_scroll.grid(row=0, column=1, sticky="ns")
        self.raw_text.configure(yscrollcommand=raw_scroll.set)

        market = ttk.Frame(market_tab, style="Market.TFrame", padding=10)
        market.grid(row=0, column=0, sticky="nsew")
        market.columnconfigure(0, weight=1)
        market.rowconfigure(2, weight=1)

        market_header = ttk.Frame(market, style="MarketToolbar.TFrame")
        market_header.grid(row=0, column=0, sticky="ew")
        market_header.columnconfigure(0, weight=1)
        ttk.Label(market_header, text="物品行情", style="MarketTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(market_header, text="行情设置", command=self.open_market_settings, style="Market.TButton").grid(row=0, column=1, sticky="e")

        market_controls = ttk.Frame(market, style="MarketToolbar.TFrame")
        market_controls.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        market_controls.columnconfigure(3, weight=1)
        ttk.Label(market_controls, text="区服", style="MarketField.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 7))
        market_region = ttk.Combobox(
            market_controls,
            textvariable=self.market_region_var,
            values=MarketSettingsDialog.REGIONS,
            state="readonly",
            width=8,
        )
        market_region.grid(row=0, column=1, sticky="w")
        market_region.bind("<<ComboboxSelected>>", self._on_market_region_selected)
        ttk.Label(market_controls, text="物品名", style="MarketField.TLabel").grid(row=0, column=2, sticky="e", padx=(14, 7))
        self.market_item_entry = ttk.Entry(market_controls, textvariable=self.market_item_var, style="Result.TEntry")
        self.market_item_entry.grid(row=0, column=3, sticky="ew")
        self.market_item_entry.bind("<Return>", self._on_market_query_enter)
        self.market_query_button = ttk.Button(market_controls, text="查询行情", command=self.query_market, style="Market.TButton")
        self.market_query_button.grid(row=0, column=4, padx=(9, 0))
        ttk.Checkbutton(
            market_controls,
            text="OCR 后自动查询",
            variable=self.market_auto_query_var,
            command=self._on_market_auto_query_toggled,
            style="Market.TCheckbutton",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Button(market_controls, text="使用 OCR 物品", command=self.use_ocr_item_for_market, style="Market.TButton").grid(
            row=1, column=3, sticky="e", pady=(7, 0)
        )
        ttk.Button(market_controls, text="打开原网站", command=lambda: webbrowser.open(MARKET_WEB_URL), style="Market.TButton").grid(
            row=1, column=4, padx=(7, 0), pady=(7, 0)
        )

        market_table_holder = ttk.Frame(market, style="Market.TFrame")
        market_table_holder.grid(row=2, column=0, sticky="nsew")
        market_table_holder.rowconfigure(0, weight=1)
        market_table_holder.columnconfigure(0, weight=1)
        columns = ("source", "item_quantity", "owner", "stall_info", "coordinate", "price")
        self.market_table = ttk.Treeview(
            market_table_holder,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Market.Treeview",
            height=5,
        )
        for column, label, width, stretch in (
            ("source", "来源", 62, False),
            ("item_quantity", "物品名*数量", 190, True),
            ("owner", "卖家", 150, True),
            ("stall_info", "摊位/商行信息", 145, True),
            ("coordinate", "位置", 250, True),
            ("price", "单价", 95, False),
        ):
            self.market_table.heading(column, text=label)
            self.market_table.column(column, width=width, minwidth=60, anchor="w", stretch=stretch)
        self.market_table.tag_configure("odd", background="#3e2119")
        self.market_table.tag_configure("even", background="#4d2920")
        self.market_table.grid(row=0, column=0, sticky="nsew")
        market_scrollbar = ttk.Scrollbar(market_table_holder, orient="vertical", command=self.market_table.yview)
        market_scrollbar.grid(row=0, column=1, sticky="ns")
        self.market_table.configure(yscrollcommand=market_scrollbar.set)
        self.market_table.bind("<<TreeviewSelect>>", self._show_market_detail)

        market_footer = ttk.Frame(market, style="MarketToolbar.TFrame")
        market_footer.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        ttk.Label(market_footer, textvariable=self.market_status_var, style="MarketNote.TLabel").pack(anchor="w")
        ttk.Label(market_footer, textvariable=self.market_detail_var, style="MarketNote.TLabel", wraplength=1160).pack(anchor="w", pady=(3, 0))

        self.status_var = tk.StringVar(value="就绪。选择游戏窗口后即可截取并识别。")
        status = ttk.Label(root, textvariable=self.status_var, style="Status.TLabel", padding=(12, 8))
        status.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self._refresh_role_binding_status()

    def _register_hotkey(self) -> None:
        try:
            import keyboard

            self.hotkey_id = keyboard.add_hotkey(DEFAULT_HOTKEY, self._on_hotkey, suppress=False)
        except ModuleNotFoundError:
            self.status_var.set("未安装 keyboard 依赖；请运行“安装国令助手依赖.cmd”后重启。")
        except Exception as error:
            self.status_var.set(f"全局热键注册失败：{error}。可使用界面按钮进行识别。")

    def _on_hotkey(self) -> None:
        self.after(0, self.quick_capture_and_recognize)

    def _close(self) -> None:
        self._save_settings()
        self.flash_monitor.stop()
        try:
            if self.flash_alert.winfo_exists():
                self.flash_alert.close()
        except AttributeError:
            pass
        if self.hotkey_id is not None:
            try:
                import keyboard

                keyboard.remove_hotkey(self.hotkey_id)
            except Exception:
                pass
        self.destroy()

    def _register_hotkey(self) -> None:
        if self._apply_hotkey(self.hotkey_var.get(), announce=False):
            return
        self.hotkey_var.set(DEFAULT_HOTKEY)
        self._apply_hotkey(DEFAULT_HOTKEY, announce=False)

    def open_monster_lookup(self) -> None:
        """Open one reusable offline monster lookup window."""
        try:
            if self.monster_lookup.winfo_exists():
                self.monster_lookup.deiconify()
                self.monster_lookup.lift()
                self.monster_lookup.focus_force()
                return
        except AttributeError:
            pass
        self.monster_lookup = MonsterLookupDialog(self)

    def open_custom_vocabulary(self) -> None:
        """Open one reusable editor for user-provided item vocabulary."""
        try:
            if self.custom_vocabulary.winfo_exists():
                self.custom_vocabulary.deiconify()
                self.custom_vocabulary.lift()
                self.custom_vocabulary.focus_force()
                return
        except AttributeError:
            pass
        self.custom_vocabulary = CustomVocabularyDialog(self)

    def open_market_settings(self) -> None:
        """Open the separate login and default-region settings dialog."""
        try:
            if self.market_settings.winfo_exists():
                self.market_settings.deiconify()
                self.market_settings.lift()
                self.market_settings.focus_force()
                return
        except AttributeError:
            pass
        self.market_settings = MarketSettingsDialog(self)

    def open_cloud_ocr_settings(self) -> None:
        """Open the optional cloud OCR configuration dialog."""
        try:
            if self.cloud_ocr_settings.winfo_exists():
                self.cloud_ocr_settings.deiconify()
                self.cloud_ocr_settings.lift()
                self.cloud_ocr_settings.focus_force()
                return
        except AttributeError:
            pass
        self.cloud_ocr_settings = CloudOcrSettingsDialog(self)

    def set_market_session(self, session: MarketSession) -> None:
        self.market_session = session
        self.market_account_var.set(session.account)
        self._save_settings()
        self.market_status_var.set(f"已登录“{session.account}”，可查询摊位、商行和寄卖行情。")

    def clear_market_session(self) -> None:
        """Remove the locally stored third-party website authorization."""
        self.market_session = None
        self._save_settings()
        self.market_status_var.set("请在“行情设置”中登录后查询。")

    def use_ocr_item_for_market(self) -> None:
        if self.last_ocr_target_kind and self.last_ocr_target_kind != "item":
            self.market_status_var.set("当前 OCR 目标不是物品，已跳过行情查询。")
            return
        item = normalize_market_keyword(self.item_var.get())
        if not item:
            self.market_status_var.set("当前没有 OCR 物品名，请手动输入后查询。")
            return
        self.market_item_var.set(item)
        self.market_item_entry.focus_set()
        if self._auto_query_market_item(item):
            return
        self.market_status_var.set(f"已带入 OCR 物品“{item}”。")

    def _on_market_query_enter(self, _event: tk.Event) -> str:
        self.query_market()
        return "break"

    def _on_market_region_selected(self, _event: tk.Event) -> None:
        self.market_last_auto_query = None
        self._save_settings()

    def _on_market_auto_query_toggled(self) -> None:
        self.market_last_auto_query = None
        self._save_settings()
        state = "已开启" if self.market_auto_query_var.get() else "已关闭"
        self.market_status_var.set(f"OCR 后自动查询{state}。")

    def _auto_query_market_item(self, item: str) -> bool:
        item = normalize_market_keyword(item)
        if not self.market_auto_query_var.get():
            return False
        query_key = (self.market_region_var.get().strip(), item)
        if not item or query_key == self.market_last_auto_query:
            return False
        if self.query_market():
            self.market_last_auto_query = query_key
            return True
        return False

    def _on_game_window_selected(self, _event: tk.Event | None = None) -> None:
        """Restore a manually bound role whenever the selected game window changes."""
        self._save_settings()
        role = self._bound_role_for_selected_window()
        self.last_detected_role = role
        self._refresh_role_binding_status()
        if role:
            self.task_role_var.set(role)
            self.status_var.set(f"已切换游戏窗口；当前已绑定角色：{role}。")
        else:
            self.status_var.set("当前窗口尚未绑定角色；请在主窗口填写角色名后点击“绑定窗口”。")

    def _bound_role_for_selected_window(self) -> str:
        title = self._window_title_from_label(self.window_var.get())
        return self.window_role_bindings.get(title, "")

    def _refresh_role_binding_status(self) -> None:
        """Keep the persistent role binding visible beside the selected window."""
        title = self._window_title_from_label(self.window_var.get())
        role = self.window_role_bindings.get(title, "")
        if not title:
            self.role_binding_var.set("请选择一个游戏窗口后再绑定角色。")
        elif role:
            self.role_binding_var.set(f"当前窗口已绑定角色：{role}")
        else:
            self.role_binding_var.set("当前窗口尚未绑定角色。")

    def _save_task_role(self, _event: tk.Event | None = None) -> str:
        self.task_role_var.set(self.task_role_var.get().strip())
        self._save_settings()
        return "break"

    def bind_selected_window_role(self) -> bool:
        title = self._window_title_from_label(self.window_var.get())
        role = self.task_role_var.get().strip()
        if not title:
            self.task_progress_status_var.set("请先在主界面选择 QQ 三国窗口。")
            return False
        if not is_role_name_candidate(role):
            self.task_progress_status_var.set("请填写有效角色名后再绑定当前窗口。")
            return False
        self.window_role_bindings[title] = role
        self.last_detected_role = role
        self._save_settings()
        self._refresh_role_binding_status()
        self.task_progress_status_var.set(f"已绑定窗口“{title}”到角色“{role}”。")
        self.status_var.set(f"当前游戏窗口已绑定角色：{role}。")
        return True

    def unbind_selected_window_role(self) -> None:
        title = self._window_title_from_label(self.window_var.get())
        if not title or title not in self.window_role_bindings:
            self.task_progress_status_var.set("当前窗口没有已保存的角色绑定。")
            return
        role = self.window_role_bindings.pop(title)
        self.last_detected_role = ""
        self._save_settings()
        self._refresh_role_binding_status()
        self.task_progress_status_var.set(f"已解除窗口“{title}”与角色“{role}”的绑定。")

    def _confirm_role_for_window(self, hwnd: int, rect: tuple[int, int, int, int]) -> None:
        try:
            image, _capture_note = self._capture_selected_game_window(hwnd, rect)
            role = self._recognize_player_role(image, hwnd)
        except Exception as error:
            logging.warning("Role confirmation failed for hwnd %s: %r", hwnd, error)
            error_text = f"角色确认失败：{error!r}"
            self.after(0, lambda: self._role_confirmation_done(hwnd, None, error_text))
        else:
            self.after(0, lambda: self._role_confirmation_done(hwnd, role, ""))

    def _role_confirmation_done(self, hwnd: int, role: str | None, error: str) -> None:
        self.role_check_in_progress = False
        selected = self.game_windows.get(self.window_var.get())
        if selected is None or selected[0] != hwnd:
            return
        if role:
            self.last_detected_role = role
            if role != self.task_role_var.get():
                self.task_role_var.set(role)
                self._save_settings()
            self.status_var.set(f"已确认当前角色：{role}。")
        elif error:
            self.status_var.set(error)
        else:
            self.status_var.set("未能从左上角角色信息区识别角色名；可在主窗口手动填写并绑定。")

    def query_market(self) -> bool:
        session = self.market_session
        keyword = normalize_market_keyword(self.market_item_var.get())
        if keyword != self.market_item_var.get():
            self.market_item_var.set(keyword)
        region = self.market_region_var.get().strip()
        if self.market_busy:
            return False
        if session is None:
            self.market_status_var.set("请先打开“行情设置”登录账号。")
            return False
        if not keyword:
            self.market_status_var.set("请输入物品名，或使用当前 OCR 结果。")
            self.market_item_entry.focus_set()
            return False
        if not region:
            self.market_status_var.set("请在“行情设置”中选择区服。")
            return False
        self._set_market_busy(True, f"正在查询“{keyword}”的摊位和商行行情...")

        def worker() -> None:
            try:
                rows = flatten_listings(self.market_client.query_listings(session, region, keyword))
            except MarketQueryError as error:
                self.after(0, self._market_query_done, [], str(error), keyword)
            else:
                self.after(0, self._market_query_done, rows, "", keyword)

        threading.Thread(target=worker, name="market-query", daemon=True).start()
        return True

    def _set_market_busy(self, busy: bool, message: str | None = None) -> None:
        self.market_busy = busy
        self.market_query_button.configure(state="disabled" if busy else "normal")
        if message:
            self.market_status_var.set(message)

    def _market_query_done(self, rows: list[dict[str, str]], error: str, keyword: str) -> None:
        if not self.winfo_exists():
            return
        self._set_market_busy(False)
        for item_id in self.market_table.get_children():
            self.market_table.delete(item_id)
        self.market_rows.clear()
        self.market_last_keyword = ""
        self.market_detail_var.set("")
        if error:
            self.market_status_var.set(f"查询失败：{error}")
            return
        for index, row in enumerate(rows):
            item_id = str(index)
            self.market_rows[item_id] = row
            item_quantity = f"{row['name']}*{row['quantity']}" if row["quantity"] != "-" else row["name"]
            self.market_table.insert(
                "",
                "end",
                iid=item_id,
                values=(row["source"], item_quantity, row["owner"], row["stall_info"], row["coordinate"], row["price"]),
                tags=("even" if index % 2 == 0 else "odd",),
            )
        learned_name = learn_market_item_name(keyword, rows)
        self.market_last_keyword = keyword
        self._update_task_records_market_price(keyword, rows)
        learned_note = f"；已学习物品名“{learned_name}”" if learned_name and learned_name != keyword else ""
        self.market_status_var.set(f"找到 {len(rows)} 条“{keyword}”行情记录{learned_note}。")

    @staticmethod
    def _lowest_market_price(rows: list[dict[str, str]]) -> float | None:
        prices: list[float] = []
        for row in rows:
            try:
                price = float(row.get("price", "").replace(",", "").strip())
            except (AttributeError, ValueError):
                continue
            if price >= 0:
                prices.append(price)
        return min(prices) if prices else None

    def _cached_market_price(self, item: str) -> float | None:
        if normalize_market_keyword(item) != self.market_last_keyword:
            return None
        return self._lowest_market_price(list(self.market_rows.values()))

    def _update_task_records_market_price(self, keyword: str, rows: list[dict[str, str]]) -> None:
        """Fill in costs after the asynchronous market result becomes available."""
        unit_price = self._lowest_market_price(rows)
        if unit_price is None:
            return
        updated = False
        key = self.last_task_record_key
        record = self.task_records.get(key) if key is not None else None
        if (
            record is not None
            and record.objective_kind == "item"
            and normalize_market_keyword(record.objective_name) == keyword
        ):
            self.task_records[key] = replace(
                record,
                unit_price=unit_price,
                total_price=unit_price * record.required_quantity,
            )
            updated = True
        if not updated:
            return
        self._save_task_records()
        try:
            if self.task_progress_dialog.winfo_exists():
                self.task_progress_dialog._refresh_table()
        except AttributeError:
            pass

    def _show_market_detail(self, _event: tk.Event) -> None:
        selected = self.market_table.selection()
        if selected:
            self.market_detail_var.set(self.market_rows[selected[0]]["detail"])

    def open_about_dialog(self) -> None:
        """Open one reusable version and update-information window."""
        try:
            if self.about_dialog.winfo_exists():
                self.about_dialog.deiconify()
                self.about_dialog.lift()
                self.about_dialog.focus_force()
                return
        except AttributeError:
            pass
        self.about_dialog = AboutDialog(self)

    def _show_changelog_on_start(self) -> None:
        if self.show_changelog_on_start_var.get() and self.winfo_exists():
            self.open_about_dialog()

    def open_task_progress_dialog(self) -> None:
        """Open the per-role local tracker for multi-step tasks."""
        try:
            if self.task_progress_dialog.winfo_exists():
                self.task_progress_dialog.deiconify()
                self.task_progress_dialog.lift()
                self.task_progress_dialog.focus_force()
                return
        except AttributeError:
            pass
        self.task_progress_dialog = TaskProgressDialog(self)

    def _save_task_records(self) -> None:
        try:
            save_task_progress(self.task_records, TASK_PROGRESS_PATH)
        except OSError:
            logging.warning("Unable to save task progress to %s", TASK_PROGRESS_PATH, exc_info=True)

    def task_round_summary_text(self, role: str) -> str:
        if not role:
            return "绑定窗口或填写当前角色后，可查看本轮已记录的三国币成本。"
        summaries = summarize_task_rounds(self.task_records, role)
        if not summaries:
            return "当前角色还没有任务记录。"
        parts: list[str] = []
        for summary in summaries:
            progress = f"{summary.recorded_steps}/{summary.total_steps}" if summary.total_steps else str(summary.recorded_steps)
            cost = f"{summary.total_cost:,.2f}".rstrip("0").rstrip(".")
            parts.append(f"{summary.task} 第 {summary.round_index} 轮 {progress} 步，已记录成本 {cost} 三国币")
        return "；".join(parts)

    def _record_task_progress(
        self,
        lines: list[str],
        detected_role: str | None = None,
        objective: TaskObjective | None = None,
        unit_price: float | None = None,
    ) -> TaskProgress | None:
        role = self._bound_role_for_selected_window() or self.task_role_var.get().strip()
        objective = objective if objective is not None else parse_task_objective(lines)
        parsed = parse_task_progress(lines)
        if parsed is None and role:
            task = find_task_name(lines)
            inferred_step = infer_unread_task_step(self.task_records, role, task, objective) if task else None
            if task and inferred_step is not None:
                parsed = ParsedTaskProgress(task, inferred_step, 0)
                logging.info(
                    "Recovered missing task step from local progress: role=%s task=%s step=%s lines=%r",
                    role, task, inferred_step, lines,
                )
        if parsed is None:
            logging.info("Task progress not recorded because no valid task step was recognized: %r", lines)
            self.task_progress_status_var.set("已识别任务目标，但未识别到有效步骤，暂未写入记录。")
            return None
        if role and role != self.task_role_var.get():
            self.task_role_var.set(role)
            self._save_settings()
        if not role:
            self.task_progress_status_var.set(f"识别到“{parsed.task}”进度，请在主窗口填写并绑定当前角色。")
            return None
        if objective is not None and objective.kind == "item" and unit_price is None:
            unit_price = self._cached_market_price(objective.name)
        record = record_task_progress(self.task_records, role, parsed, objective, unit_price)
        self.last_task_record_key = task_record_key(record)
        self._save_task_records()
        cost_note = f" · {record.display_cost}" if record.objective_kind == "item" else ""
        self.task_progress_status_var.set(
            f"已记录：{record.role} · {record.task} · 第 {record.round_index} 轮 · {record.display_progress}{cost_note}"
        )
        try:
            if self.task_progress_dialog.winfo_exists():
                self.task_progress_dialog._refresh_table()
        except AttributeError:
            pass
        return record

    def open_flash_alert(self) -> None:
        """Open the integrated Windows taskbar-flash sound reminder."""
        try:
            if self.flash_alert.winfo_exists():
                self.flash_alert.deiconify()
                self.flash_alert.lift()
                self.flash_alert.focus_force()
                return
        except AttributeError:
            pass
        self.flash_alert = FlashAlertDialog(self)

    def refresh_flash_windows(self) -> None:
        previous_title = self._window_title_from_label(self.flash_window_var.get())
        self.flash_window_handles.clear()
        for hwnd, title in list_visible_windows():
            label = f"{title}  [窗口 {hwnd}]"
            self.flash_window_handles[label] = hwnd
        restored = next(
            (label for label in self.flash_window_handles if self._window_title_from_label(label) == previous_title),
            "",
        )
        self.flash_window_var.set(restored)

    def _restore_flash_monitor(self) -> None:
        if self.flash_enabled_var.get():
            self.toggle_flash_monitor(save=False)

    def toggle_flash_monitor(self, save: bool = True) -> None:
        if self.flash_enabled_var.get():
            if not self.flash_window_handles:
                self.refresh_flash_windows()
            try:
                self.flash_monitor.start()
            except RuntimeError as error:
                self.flash_enabled_var.set(False)
                self.flash_status_var.set(f"闪烁提醒启动失败：{error}")
            else:
                self.flash_status_var.set("闪烁提醒：监听中")
        else:
            self.flash_monitor.stop()
            self.flash_status_var.set("闪烁提醒已关闭")
        if save:
            self._save_settings()

    def _process_flash_events(self) -> None:
        for _ in range(FLASH_EVENTS_PER_TICK):
            try:
                event = self.flash_events.get_nowait()
            except Empty:
                break
            selected_hwnd = self.flash_window_handles.get(self.flash_window_var.get())
            if not matches_event(
                event,
                self.flash_title_filter_var.get(),
                selected_hwnd,
                self.flash_target_mode_var.get(),
            ):
                continue
            now = time.monotonic()
            last = self.flash_last_alert.get(event.hwnd)
            if last is not None and now - last < self._flash_cooldown_seconds():
                continue
            self.flash_last_alert[event.hwnd] = now
            success = play_sound(self.flash_sound_mode_var.get(), self.flash_wav_path_var.get())
            message = f"{'已提醒' if success else '声音播放失败'}：{event.title}"
            self.flash_status_var.set(f"闪烁提醒：{message}")
            self.status_var.set(f"闪烁提醒：{message}")
            try:
                if self.flash_alert.winfo_exists():
                    self.flash_alert._add_report(f"{message}，{event.source}")
            except AttributeError:
                pass
        if self.winfo_exists():
            self.after(100, self._process_flash_events)

    @staticmethod
    def _window_title_from_label(label: str) -> str:
        return label.partition("  [窗口")[0]

    def _save_settings(self, _event: tk.Event | None = None) -> None:
        self._auto_interval_ms()
        selected_window_title = self._window_title_from_label(self.window_var.get())
        if selected_window_title:
            self.preferred_window_title = selected_window_title
        save_user_settings({
            "capture_method": self.capture_method_var.get(),
            "interval_seconds": float(self.interval_var.get()),
            "hotkey": self.hotkey_var.get(),
            "window_title": self.preferred_window_title,
            "flash_title_filter": self.flash_title_filter_var.get(),
            "flash_target_mode": self.flash_target_mode_var.get(),
            "flash_window_title": self._window_title_from_label(self.flash_window_var.get()),
            "flash_sound_mode": self.flash_sound_mode_var.get(),
            "flash_wav_path": self.flash_wav_path_var.get(),
            "flash_cooldown_seconds": self._flash_cooldown_seconds(),
            "flash_enabled": self.flash_enabled_var.get(),
            "task_tracker_role": self.task_role_var.get(),
            "window_role_bindings": self.window_role_bindings,
            "show_changelog_on_start": self.show_changelog_on_start_var.get(),
            "market_account": self.market_account_var.get(),
            "market_token": self.market_session.token if self.market_session else "",
            "market_user_id": self.market_session.user_id if self.market_session else "",
            "market_region": self.market_region_var.get(),
            "market_auto_query": self.market_auto_query_var.get(),
            "ocr_mode": self.ocr_mode_var.get(),
            "cloud_ocr_token": self.cloud_ocr_token_var.get().strip(),
            "cloud_ocr_model": self.cloud_ocr_model_var.get().strip() or DEFAULT_CLOUD_OCR_MODEL,
            "cloud_ocr_api_url": self.cloud_ocr_api_url_var.get().strip(),
        })

    def _flash_cooldown_seconds(self) -> float:
        try:
            value = float(self.flash_cooldown_var.get())
        except (TypeError, ValueError):
            value = DEFAULT_FLASH_COOLDOWN_SECONDS
        value = min(MAX_FLASH_COOLDOWN_SECONDS, max(MIN_FLASH_COOLDOWN_SECONDS, value))
        self.flash_cooldown_var.set(f"{value:g}")
        return value

    def _remove_hotkey(self) -> None:
        if self.hotkey_id is None:
            return
        try:
            import keyboard

            keyboard.remove_hotkey(self.hotkey_id)
        except Exception:
            pass
        finally:
            self.hotkey_id = None

    def _apply_hotkey(self, hotkey: str, announce: bool = True) -> bool:
        try:
            import keyboard

            new_hotkey_id = keyboard.add_hotkey(hotkey, self._on_hotkey, suppress=False)
        except ModuleNotFoundError:
            self.status_var.set("未安装 keyboard 依赖；请运行“安装国令助手依赖.cmd”后重启。")
            return False
        except Exception as error:
            self.status_var.set(f"快捷键“{hotkey}”注册失败：{error}")
            return False
        self._remove_hotkey()
        self.hotkey_id = new_hotkey_id
        self.hotkey_var.set(hotkey)
        self._save_settings()
        if announce:
            self.status_var.set(f"快捷键已更新为 {hotkey}。")
        return True

    def record_hotkey(self) -> None:
        previous_hotkey = self.hotkey_var.get()
        self._remove_hotkey()

        def complete(hotkey: str) -> None:
            if not self._apply_hotkey(hotkey):
                self._apply_hotkey(previous_hotkey, announce=False)
                self.status_var.set(f"快捷键未更改，仍为 {previous_hotkey}。")

        def cancel() -> None:
            self._apply_hotkey(previous_hotkey, announce=False)
            self.status_var.set("已取消录制快捷键。")

        HotkeyRecorder(self, complete, cancel)

    def capture_and_select(self) -> None:
        self.withdraw()
        self.after(250, self._grab_screen)

    def _grab_screen(self) -> None:
        try:
            image = ImageGrab.grab(all_screens=True).convert("RGB")
        except Exception as error:
            self.deiconify()
            messagebox.showerror("截图失败", str(error), parent=self)
            return
        self.source_image = image
        self.deiconify()
        CropOverlay(self, image, self.set_crop)

    def refresh_game_windows(self) -> None:
        previous = self.window_var.get()
        self.game_windows.clear()
        for hwnd, title, rect in find_qqsg_windows():
            label = f"{title}  [窗口 {hwnd}]"
            self.game_windows[label] = (hwnd, rect)
        values = tuple(self.game_windows)
        self.window_combo.configure(values=values)
        if previous in self.game_windows:
            self.window_var.set(previous)
        elif self.preferred_window_title:
            restored = next(
                (
                    label for label in values
                    if self._window_title_from_label(label) == self.preferred_window_title
                ),
                None,
            )
            if restored:
                self.window_var.set(restored)
            elif values:
                self.window_var.set(values[0])
        elif values:
            self.window_var.set(values[0])
            self.status_var.set(f"已发现 {len(values)} 个 QQ 三国窗口；可直接点击“截图选中窗口”。")
        else:
            self.window_var.set("")
            self.status_var.set("未发现 QQ 三国窗口。请先启动游戏，或使用手动截图框选。")

        role = self._bound_role_for_selected_window()
        if role:
            self.last_detected_role = role
            self.task_role_var.set(role)
        self._refresh_role_binding_status()

    def capture_selected_window(self) -> None:
        selection = self.game_windows.get(self.window_var.get())
        if not selection:
            self.refresh_game_windows()
            selection = self.game_windows.get(self.window_var.get())
        if not selection:
            messagebox.showinfo("提示", "未选择 QQ 三国窗口。请先启动游戏后点击“刷新窗口”。", parent=self)
            return
        try:
            hwnd, rect = selection
            self.source_image, capture_note = self._capture_selected_game_window(hwnd, rect)
            self.set_source_for_location(self.source_image, hwnd)
            self.status_var.set(f"已使用 {capture_note} 截取完整窗口；点击“识别并复制”即可自动定位任务区域。")
        except Exception as error:
            self._show_error(f"窗口截图失败：{error!r}")

    def _capture_selected_game_window(
        self, hwnd: int, rect: tuple[int, int, int, int]
    ) -> tuple[Image.Image, str]:
        if self.capture_method_var.get().startswith("WGC"):
            try:
                return capture_game_window_wgc(hwnd), "WGC"
            except Exception as error:
                logging.warning("WGC capture failed for hwnd %s; falling back to PrintWindow: %r", hwnd, error)
                return capture_game_window(hwnd, rect), "PrintWindow（WGC 失败回退）"
        return capture_game_window(hwnd, rect), "PrintWindow"

    def quick_capture_and_recognize(self, skip_unchanged: bool = False) -> None:
        """热键/实时模式使用的无交互快速识别。"""
        if self.ocr_in_progress:
            return
        try:
            selection = self.game_windows.get(self.window_var.get())
            if not selection:
                self.refresh_game_windows()
                selection = self.game_windows.get(self.window_var.get())
            if selection:
                hwnd, rect = selection
                self._capture_and_recognize_window(hwnd, rect, skip_unchanged)
            else:
                self.source_image = ImageGrab.grab(all_screens=True).convert("RGB")
                self.set_source_for_location(self.source_image)
                self.recognize(skip_unchanged=skip_unchanged)
        except Exception as error:
            self._show_error(f"快捷截图失败：{error!r}")

    def _capture_and_recognize_window(
        self, hwnd: int, rect: tuple[int, int, int, int], skip_unchanged: bool
    ) -> None:
        try:
            self.source_image, _capture_note = self._capture_selected_game_window(hwnd, rect)
            self.set_source_for_location(self.source_image, hwnd)
            self.recognize(skip_unchanged=skip_unchanged)
        except Exception as error:
            self._show_error(f"快捷截图失败：{error!r}")

    def _auto_interval_ms(self, show_error: bool = False) -> int:
        try:
            seconds = float(self.interval_var.get())
            if not MIN_AUTO_INTERVAL_SECONDS <= seconds <= MAX_AUTO_INTERVAL_SECONDS:
                raise ValueError
        except (TypeError, ValueError):
            seconds = DEFAULT_AUTO_INTERVAL_SECONDS
            self.interval_var.set(f"{seconds:g}")
            if show_error:
                self.status_var.set(
                    f"实时识别间隔需在 {MIN_AUTO_INTERVAL_SECONDS:g} 到 {MAX_AUTO_INTERVAL_SECONDS:g} 秒之间，已恢复为 {seconds:g} 秒。"
                )
        else:
            self.interval_var.set(f"{seconds:g}")
        return round(seconds * 1000)

    def _validate_auto_interval(self, _event: tk.Event | None = None) -> None:
        self._auto_interval_ms(show_error=self.auto_var.get())
        self._save_settings()

    def toggle_auto(self) -> None:
        self._auto_generation += 1
        if self.auto_var.get():
            interval_ms = self._auto_interval_ms(show_error=True)
            self.status_var.set(f"实时识别已开启：每 {interval_ms / 1000:g} 秒检查一次任务区域。")
            self._auto_tick(self._auto_generation)
        else:
            self.status_var.set(f"实时识别已关闭；可使用 {self.hotkey_var.get()} 快捷识别。")

    def _auto_tick(self, generation: int) -> None:
        if not self.auto_var.get() or generation != self._auto_generation:
            return
        self.quick_capture_and_recognize(skip_unchanged=True)
        self.after(self._auto_interval_ms(), lambda: self._auto_tick(generation))

    def load_image(self) -> None:
        path = filedialog.askopenfilename(title="选择游戏截图", filetypes=(("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")))
        if not path:
            return
        try:
            self.source_image = Image.open(Path(path)).convert("RGB")
            self.set_source_for_location(self.source_image)
        except Exception as error:
            messagebox.showerror("载入失败", str(error), parent=self)

    def default_crop(self) -> None:
        if not self.source_image:
            messagebox.showinfo("提示", "请先截取屏幕或载入游戏截图。", parent=self)
            return
        self.set_crop(self._task_region(self.source_image))

    @staticmethod
    def _task_region(image: Image.Image) -> Image.Image:
        return image.crop(default_task_crop_box(image))

    def set_crop(self, image: Image.Image) -> None:
        self.crop_image = image
        self.locate_from_source = False
        self.source_window_handle = None
        preview = image.copy()
        preview.thumbnail((400, 380))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_image, text="")
        self.recognize_button.configure(state="normal")
        self.status_var.set("已选择区域，点击“识别并复制”。")

    def set_source_for_location(self, image: Image.Image, window_handle: int | None = None) -> None:
        """保留完整窗口，由识别线程用任务关键词决定最终裁剪区域。"""
        self.source_image = image
        self.crop_image = None
        self.locate_from_source = True
        self.source_window_handle = window_handle
        preview = image.copy()
        preview.thumbnail((400, 380))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_image, text="")
        self.recognize_button.configure(state="normal")
        self.status_var.set("已完整截取窗口；点击“识别并复制”将自动定位任务追踪区域。")

    def recognize(self, skip_unchanged: bool = False) -> None:
        if self.ocr_in_progress:
            return
        cloud_mode = self.ocr_mode_var.get() == "cloud"
        if cloud_mode:
            self.cached_task_region = None
            self.cached_task_window = None
        if self.locate_from_source and self.source_image:
            image = self.source_image.copy()
            locate_from_source = True
            window_handle = self.source_window_handle
            cached_region = self.cached_task_region if window_handle == self.cached_task_window else None
        elif self.crop_image:
            image = self.crop_image.copy()
            locate_from_source = False
            window_handle = None
            cached_region = None
        else:
            return
        self.ocr_in_progress = True
        self.recognize_button.configure(state="disabled")
        if cached_region:
            self.status_var.set("正在识别缓存的任务区域……")
        elif cloud_mode:
            self.status_var.set("正在上传截图并等待云端 OCR 识别……")
        else:
            self.status_var.set("正在定位任务区域并识别中文文字；首次使用会初始化 OCR 模型，请稍候……")
        threading.Thread(
            target=self._recognize_worker,
            args=(image, locate_from_source, window_handle, cached_region, skip_unchanged),
            daemon=True,
        ).start()

    def _recognize_worker(
        self,
        image: Image.Image,
        locate_from_source: bool,
        window_handle: int | None,
        cached_region: tuple[float, float, float, float] | None,
        skip_unchanged: bool,
    ) -> None:
        try:
            cloud_mode = self.ocr_mode_var.get() == "cloud"
            full_window_image = image.copy() if locate_from_source and window_handle is not None and not cloud_mode else None
            detected_role = None
            location_note = "手动选择的区域"
            used_cached_region = cached_region is not None
            region_for_cache: tuple[float, float, float, float] | None = None
            if cached_region:
                image = self._crop_relative_region(image, cached_region)
                location_note = "缓存定位：任务追踪区域"
            elif locate_from_source:
                if cloud_mode:
                    crop_box = cloud_task_crop_box(image)
                    image = image.crop(crop_box)
                    location_note = "云端快速模式：右侧任务区域"
                else:
                    full_image = self._limit_ocr_size(image)
                    full_entries = self._ocr_image_entries(full_image)
                    image, location_note, crop_box = locate_task_panel(full_image, full_entries)
                    region_for_cache = self._relative_region(crop_box, full_image.size)

            signature = self._task_signature(image)
            if used_cached_region and should_skip_unchanged_task(
                skip_unchanged, signature, self.last_task_signature
            ):
                self.after(0, self._show_unchanged_frame)
                return

            lines, final_crop = self._recognize_task_crop(image)

            # A misleading full-window anchor can select player names or scenery.
            # Fall back before presenting or caching that crop if it has no task text.
            if full_window_image is not None and not has_task_panel_context(lines):
                fallback_box = default_task_crop_box(full_window_image)
                fallback_image = full_window_image.crop(fallback_box)
                fallback_lines, fallback_crop = self._recognize_task_crop(fallback_image)
                if has_task_panel_context(fallback_lines) or extract_candidate(fallback_lines):
                    lines = fallback_lines
                    final_crop = fallback_crop
                    signature = self._task_signature(fallback_image)
                    location_note = "自动定位未找到任务文字，已使用右侧任务区域回退"
                    region_for_cache = self._relative_region(fallback_box, full_window_image.size)
            self.after(
                0,
                lambda: self._show_result(
                    lines, final_crop, location_note, window_handle, region_for_cache, signature, detected_role
                ),
            )
        except CloudOcrError as error:
            error_text = f"云端 OCR：{error}"
            self.after(0, lambda: self._show_error(error_text))
        except ModuleNotFoundError:
            self.after(0, lambda: self._show_error("缺少 OCR 依赖。请先双击“安装国令助手依赖.cmd”，完成后重新启动程序。"))
        except Exception as error:
            logging.exception("OCR engine failed")
            # Python clears an exception variable after an ``except`` block. Keep
            # its rendered text for the Tk callback, which runs later.
            error_text = f"识别失败：{error!r}"
            self.after(0, lambda: self._show_error(error_text))

    def _recognize_task_crop(self, image: Image.Image) -> tuple[list[str], Image.Image]:
        """Run the task-area OCR pass, including a higher-contrast retry."""
        target_size = cloud_task_ocr_target_size(image.size) if self.ocr_mode_var.get() == "cloud" else task_ocr_target_size(image.size)
        scaled = image.resize(target_size, Image.Resampling.LANCZOS)
        enhanced = ImageEnhance.Sharpness(ImageEnhance.Contrast(ImageOps.grayscale(scaled)).enhance(2.2)).enhance(2.0)
        entries = self._ocr_image_entries(scaled)
        if not entries and self.ocr_mode_var.get() != "cloud":
            entries = self._ocr_image_entries(enhanced.convert("RGB"))
        lines = [text for _box, text in entries]
        final_crop = scaled.resize(image.size, Image.Resampling.LANCZOS)
        return lines, final_crop

    def _ensure_ocr_engine(self) -> None:
        """Initialize the shared OCR engine once for task and role recognition."""
        with self.ocr_lock:
            if self.ocr_engine is not None:
                return
            from paddleocr import PaddleOCR

            ensure_writable_error_stream()
            self.ocr_engine = PaddleOCR(
                lang="ch",
                use_angle_cls=False,
                show_log=False,
                **ocr_model_directories(),
            )

    def _recognize_player_role(self, image: Image.Image, hwnd: int | None = None) -> str | None:
        """Read the character name from QQ SG's upper-left player information HUD."""
        player_crop = image.crop(player_info_crop_box(image, game_client_box(hwnd, image)))
        scaled = player_crop.resize(player_ocr_target_size(player_crop.size), Image.Resampling.LANCZOS)
        entries = self._ocr_image_entries(scaled)
        role = find_hud_role_name(entries)
        if role:
            return correct_role_name(role)
        name_crop = crop_player_name_from_level(scaled, entries)
        if name_crop is not None:
            name_entries = self._ocr_image_entries(name_crop)
            role = find_hud_role_name(name_entries, allow_isolated_name=True)
            if role:
                return correct_role_name(role)
        enhanced = ImageEnhance.Sharpness(ImageEnhance.Contrast(ImageOps.grayscale(scaled)).enhance(2.4)).enhance(2.0)
        if self.ocr_mode_var.get() == "cloud":
            return None
        entries = self._ocr_image_entries(enhanced.convert("RGB"))
        role = find_hud_role_name(entries)
        if role:
            return correct_role_name(role)
        name_crop = crop_player_name_from_level(enhanced, entries)
        if name_crop is None:
            return None
        name_entries = self._ocr_image_entries(name_crop.convert("RGB"))
        role = find_hud_role_name(name_entries, allow_isolated_name=True)
        return correct_role_name(role) if role else None

    @staticmethod
    def _limit_ocr_size(image: Image.Image, max_edge: int = 1600) -> Image.Image:
        """限制整窗 OCR 的边长，兼顾不同分辨率下的速度与文字清晰度。"""
        if max(image.size) <= max_edge:
            return image
        scale = max_edge / max(image.size)
        return image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)

    @staticmethod
    def _relative_region(
        crop_box: tuple[int, int, int, int], image_size: tuple[int, int]
    ) -> tuple[float, float, float, float]:
        width, height = image_size
        return tuple(value / divisor for value, divisor in zip(crop_box, (width, height, width, height)))

    @staticmethod
    def _crop_relative_region(image: Image.Image, region: tuple[float, float, float, float]) -> Image.Image:
        width, height = image.size
        left, top, right, bottom = (round(value * divisor) for value, divisor in zip(region, (width, height, width, height)))
        return image.crop((max(0, left), max(0, top), min(width, right), min(height, bottom)))

    @staticmethod
    def _task_signature(image: Image.Image) -> bytes:
        return image.convert("L").resize((64, 64), Image.Resampling.BILINEAR).tobytes()

    @staticmethod
    def _signature_difference(first: bytes, second: bytes) -> float:
        return sum(abs(left - right) for left, right in zip(first, second)) / len(first)

    def _ocr_entries(self, image_array: object) -> list[tuple[list[list[float]], str]]:
        with self.ocr_lock:
            result = self.ocr_engine.ocr(image_array, cls=False)
        entries: list[tuple[list[list[float]], str]] = []
        for entry in result[0] or []:
            if not entry or not entry[1][0].strip():
                continue
            box = [[float(point[0]), float(point[1])] for point in entry[0]]
            entries.append((box, entry[1][0].strip()))
        return entries

    def _ocr_image_entries(self, image: Image.Image) -> list[tuple[list[list[float]], str]]:
        """Recognize a PIL image while keeping cloud mode free of local OCR imports."""
        if self.ocr_mode_var.get() == "cloud":
            return self.cloud_ocr_client.recognize(
                image,
                self.cloud_ocr_token_var.get(),
                self.cloud_ocr_model_var.get().strip() or DEFAULT_CLOUD_OCR_MODEL,
                self.cloud_ocr_api_url_var.get(),
            )
        import numpy as np

        self._ensure_ocr_engine()
        return self._ocr_entries(np.array(image))

    def _show_unchanged_frame(self) -> None:
        self.ocr_in_progress = False
        self.recognize_button.configure(state="normal")
        self.status_var.set("实时识别：任务区域未发生明显变化，已跳过 OCR。")

    def _show_result(
        self,
        lines: list[str],
        crop_image: Image.Image,
        location_note: str,
        window_handle: int | None,
        region_for_cache: tuple[float, float, float, float] | None,
        signature: bytes,
        detected_role: str | None,
    ) -> None:
        self.crop_image = crop_image
        self.locate_from_source = False
        preview = crop_image.copy()
        preview.thumbnail((400, 380))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_image, text="")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", "\n".join(lines) if lines else "未识别到文字，请重新框选更紧凑的任务要求区域。")
        objective = parse_task_objective(lines)
        raw_candidate = objective.name if objective else extract_candidate(lines)
        candidate = raw_candidate if objective and objective.kind != "item" else correct_item_name(raw_candidate)
        npc_name = correct_npc_name(objective.npc) if objective and objective.npc else ""
        if objective and objective.kind == "npc":
            candidate = npc_name or correct_npc_name(candidate)
        target_kind = objective.kind if objective else ""
        monster_target = is_monster_task_target(lines, candidate, target_kind)
        non_item_target = target_kind in {"monster", "npc"} or monster_target
        self.last_ocr_is_monster_target = monster_target
        self.last_ocr_target_kind = "monster" if monster_target else target_kind
        objective_for_record = replace(objective, name=candidate) if objective and objective.kind == "item" else objective
        unit_price = self._cached_market_price(candidate) if objective_for_record and objective_for_record.kind == "item" else None
        progress_record = self._record_task_progress(lines, detected_role, objective_for_record, unit_price)
        progress_note = (
            f"；已记录 {progress_record.task} {progress_record.display_progress}" if progress_record else ""
        )
        self.last_task_signature = signature
        role = (self._bound_role_for_selected_window() or self.task_role_var.get().strip()) if progress_record else ""
        self.recognized_role_var.set(role or "未识别")
        self.target_name_var.set(candidate or "未识别")
        self.target_quantity_var.set(objective.display_quantity if objective else "-")
        self.npc_name_var.set(npc_name or "-")
        if window_handle is not None and region_for_cache is not None:
            self.cached_task_window = window_handle
            self.cached_task_region = region_for_cache
        self.ocr_in_progress = False
        self.recognize_button.configure(state="normal")
        if candidate:
            self.item_var.set(candidate)
            if not non_item_target:
                self.market_item_var.set(candidate)
            self.last_valid_item = candidate
            self.copy_item(silent=True)
            if non_item_target:
                target_label = "怪物" if monster_target else "NPC"
                self.market_status_var.set(f"当前 OCR 目标是{target_label}，已跳过自动行情查询。")
                self.status_var.set(f"{location_note}；已识别{target_label}“{candidate}”并复制到剪贴板，未查询行情{progress_note}。")
            elif self._auto_query_market_item(candidate):
                self.status_var.set(f"{location_note}；已识别“{candidate}”并复制到剪贴板，正在自动查询行情{progress_note}。")
            else:
                self.status_var.set(f"{location_note}；已识别“{candidate}”并复制到剪贴板{progress_note}。")
        else:
            if self.last_valid_item:
                self.item_var.set(self.last_valid_item)
                self.status_var.set(f"{location_note}；未发现完整道具行，已保留上次结果“{self.last_valid_item}”{progress_note}。")
            else:
                self.status_var.set(f"{location_note}；未发现完整道具行，可能被游戏界面遮挡，请关闭界面后重试{progress_note}。")

    def _show_error(self, text: str) -> None:
        logging.error(text)
        self.ocr_in_progress = False
        self.recognize_button.configure(state="normal")
        self.status_var.set(text)
        messagebox.showerror("识别失败", text, parent=self)

    def copy_item(self, silent: bool = False) -> None:
        item = self.item_var.get().strip()
        if not item:
            if not silent:
                messagebox.showinfo("提示", "没有可复制的物品名。", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(item)
        self.update()
        if not silent:
            self.status_var.set(f"已复制“{item}”到剪贴板。")


def report_unhandled_exception(exc_type: type[BaseException], value: BaseException, trace: object) -> None:
    logging.error("Uncaught application exception:\n%s", "".join(traceback.format_exception(exc_type, value, trace)))
    try:
        messagebox.showerror("国令助手启动失败", f"发生错误：{value}\n\n详细日志：{LOG_PATH}")
    except Exception:
        pass


def main() -> None:
    sys.excepthook = report_unhandled_exception
    logging.info("Starting GuoLingZhuShou GUI")
    app = GuolingTaskOcr()
    app.report_callback_exception = report_unhandled_exception
    app.mainloop()


if __name__ == "__main__":
    main()
