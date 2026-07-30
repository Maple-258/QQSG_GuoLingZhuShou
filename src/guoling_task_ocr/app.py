"""国令助手：识别任务区域中的物品名并复制到剪贴板。"""

from __future__ import annotations

import re
import json
import threading
import tkinter as tk
import logging
import sys
import traceback
import ctypes
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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
LOG_PATH = RUNTIME_DIR / "ocr_app.log"
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TASK_CROP = (0.80, 0.41, 0.985, 0.54)
DEFAULT_HOTKEY = "ctrl+alt+g"
DEFAULT_AUTO_INTERVAL_SECONDS = 2.0
MIN_AUTO_INTERVAL_SECONDS = 0.5
MAX_AUTO_INTERVAL_SECONDS = 60.0
TASK_OCR_SCALE = 4
UNCHANGED_FRAME_THRESHOLD = 2.0
WGC_CAPTURE_TIMEOUT_SECONDS = 3.0
TASK_CONTEXT_TERMS = ("任务", "国令", "NPC", "需要", "收集", "提交", "消灭")
ITEM_ALIASES_PATH = DATA_DIR / "道具OCR纠错.json"
ITEM_VOCABULARY_PATH = DATA_DIR / "官方道具词表.json"
EXCLUDED_WORDS = {
    "任务追踪", "国令慕贤", "高级国令", "当前", "任务", "目标", "进度", "完成",
    "可用", "点击", "道具", "物品", "材料", "需求", "需要", "所需", "收集", "寻找",
    "上交", "提交", "获得", "NPC",
}
ITEM_PATTERNS = (
    r"(?:需求|需要|所需|物品|道具|材料|收集|寻找|上交|提交|获得)[：: \t]*([\u4e00-\u9fff]{2,8}(?:-\d+级)?)",
    r"([\u4e00-\u9fff]{2,8}(?:-\d+级)?)\s*\d+\s*/\s*\d+",
)


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


def match_official_item_name(candidate: str) -> str:
    """只接受唯一的近似命中，防止把不相关的菜单文字强行替换。"""
    names = load_official_item_names()
    if candidate in names or len(candidate) < 3:
        return candidate
    matches = [
        name for name in names
        if len(name) == len(candidate) and name[:2] == candidate[:2] and _edit_distance(name, candidate) == 1
    ]
    return matches[0] if len(matches) == 1 else candidate


def correct_item_name(candidate: str) -> str:
    aliases = load_item_aliases()
    corrected = aliases.get(candidate, candidate)
    if corrected == candidate:
        for mistaken, expected in aliases.items():
            suffix = candidate.removeprefix(mistaken)
            if suffix != candidate and re.fullmatch(r"-\d+级", suffix):
                corrected = expected + suffix
                break
    return match_official_item_name(corrected)


def _entry_box(entry: tuple[list[list[float]], str]) -> tuple[float, float, float, float]:
    points, _text = entry
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _normalise_ocr_text(text: str) -> str:
    return re.sub(r"[\s：:，,。．·_\-]", "", text)


def default_task_crop_box(image: Image.Image) -> tuple[int, int, int, int]:
    width, height = image.size
    left, top, right, bottom = TASK_CROP
    return int(width * left), int(height * top), int(width * right), int(height * bottom)


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


class GuolingTaskOcr(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("国令助手")
        self.geometry("1140x790")
        self.minsize(960, 620)
        self.configure(bg="#edf1f5")
        self.source_image: Image.Image | None = None
        self.crop_image: Image.Image | None = None
        self.locate_from_source = False
        self.source_window_handle: int | None = None
        self.cached_task_region: tuple[float, float, float, float] | None = None
        self.cached_task_window: int | None = None
        self.last_task_signature: bytes | None = None
        self.last_valid_item = ""
        self.preview_image: ImageTk.PhotoImage | None = None
        self.ocr_engine = None
        self.ocr_in_progress = False
        self.hotkey_id = None
        self.auto_var = tk.BooleanVar(value=False)
        self.interval_var = tk.StringVar(value=f"{DEFAULT_AUTO_INTERVAL_SECONDS:g}")
        self.hotkey_var = tk.StringVar(value=DEFAULT_HOTKEY)
        self._auto_generation = 0
        self.window_var = tk.StringVar()
        self.capture_method_var = tk.StringVar(value="PrintWindow（兼容）")
        self.game_windows: dict[str, tuple[int, tuple[int, int, int, int]]] = {}
        self._style()
        self._build()
        self._register_hotkey()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(200, self.refresh_game_windows)

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
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="游戏窗口", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.window_combo = ttk.Combobox(source, textvariable=self.window_var, state="readonly", width=36)
        self.window_combo.grid(row=0, column=1, sticky="ew")
        ttk.Button(source, text="刷新", command=self.refresh_game_windows).grid(row=0, column=2, padx=(8, 14))
        ttk.Label(source, text="截图方式", style="Field.TLabel").grid(row=0, column=3, padx=(0, 8))
        self.capture_method_combo = ttk.Combobox(
            source,
            textvariable=self.capture_method_var,
            values=("WGC（后台，高效）", "PrintWindow（兼容）"),
            state="readonly",
            width=17,
        )
        self.capture_method_combo.grid(row=0, column=4, sticky="w")
        ttk.Button(source, text="截取窗口", command=self.capture_selected_window, style="Primary.TButton").grid(row=0, column=5, padx=(12, 0))

        tools = ttk.Frame(root, style="Surface.TFrame", padding=(14, 9))
        tools.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        tools.columnconfigure(7, weight=1)
        ttk.Button(tools, text="框选屏幕", command=self.capture_and_select).grid(row=0, column=0, sticky="w")
        ttk.Button(tools, text="载入截图", command=self.load_image).grid(row=0, column=1, padx=(7, 0))
        ttk.Button(tools, text="默认区域", command=self.default_crop).grid(row=0, column=2, padx=(7, 14))
        ttk.Checkbutton(tools, text="实时识别", variable=self.auto_var, command=self.toggle_auto).grid(row=0, column=3, sticky="w")
        ttk.Label(tools, text="间隔", style="Field.TLabel").grid(row=0, column=4, padx=(12, 5))
        self.interval_spinbox = ttk.Spinbox(
            tools,
            from_=MIN_AUTO_INTERVAL_SECONDS,
            to=MAX_AUTO_INTERVAL_SECONDS,
            increment=0.5,
            textvariable=self.interval_var,
            width=5,
            justify="center",
        )
        self.interval_spinbox.grid(row=0, column=5)
        self.interval_spinbox.bind("<FocusOut>", self._validate_auto_interval)
        self.interval_spinbox.bind("<Return>", self._validate_auto_interval)
        ttk.Label(tools, text="秒", style="Field.TLabel").grid(row=0, column=6, padx=(5, 14))
        ttk.Label(tools, text="快捷键", style="Field.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(tools, textvariable=self.hotkey_var, style="Field.TLabel", width=14).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Button(tools, text="录制", command=self.record_hotkey).grid(row=1, column=2, padx=(6, 0), pady=(8, 0))
        ttk.Button(tools, text="快捷识别", command=self.quick_capture_and_recognize, style="Quick.TButton").grid(row=1, column=8, padx=(8, 0), pady=(8, 0))
        self.recognize_button = ttk.Button(tools, text="识别并复制", command=self.recognize, state="disabled", style="Primary.TButton")
        self.recognize_button.grid(row=1, column=9, padx=(8, 0), pady=(8, 0))

        content = ttk.Frame(root)
        content.grid(row=3, column=0, sticky="nsew")
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
        result.rowconfigure(4, weight=1)
        result.columnconfigure(0, weight=1)
        ttk.Label(result, text="识别结果", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(result, text="任务物品名", style="Field.TLabel").grid(row=1, column=0, sticky="w")
        self.item_var = tk.StringVar()
        self.item_entry = ttk.Entry(result, textvariable=self.item_var, style="Result.TEntry")
        self.item_entry.grid(row=2, column=0, sticky="ew", pady=(4, 13))
        ttk.Button(result, text="复制", command=self.copy_item, style="Primary.TButton").grid(row=2, column=1, padx=(8, 0), pady=(4, 13))
        ttk.Label(result, text="OCR 原始文本", style="Field.TLabel").grid(row=3, column=0, columnspan=2, sticky="nw")
        raw_holder = ttk.Frame(result, style="Surface.TFrame")
        raw_holder.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        raw_holder.rowconfigure(0, weight=1)
        raw_holder.columnconfigure(0, weight=1)
        self.raw_text = tk.Text(
            raw_holder,
            height=9,
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

        self.status_var = tk.StringVar(value="就绪。选择游戏窗口后即可截取并识别。")
        status = ttk.Label(root, textvariable=self.status_var, style="Status.TLabel", padding=(12, 8))
        status.grid(row=4, column=0, sticky="ew", pady=(12, 0))

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
        if self.hotkey_id is not None:
            try:
                import keyboard

                keyboard.remove_hotkey(self.hotkey_id)
            except Exception:
                pass
        self.destroy()

    def _register_hotkey(self) -> None:
        self._apply_hotkey(DEFAULT_HOTKEY, announce=False)

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
        elif values:
            self.window_var.set(values[0])
            self.status_var.set(f"已发现 {len(values)} 个 QQ 三国窗口；可直接点击“截图选中窗口”。")
        else:
            self.window_var.set("")
            self.status_var.set("未发现 QQ 三国窗口。请先启动游戏，或使用手动截图框选。")

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

    def quick_capture_and_recognize(self) -> None:
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
                self._capture_and_recognize_window(hwnd, rect)
            else:
                self.source_image = ImageGrab.grab(all_screens=True).convert("RGB")
                self.set_source_for_location(self.source_image)
                self.recognize()
        except Exception as error:
            self._show_error(f"快捷截图失败：{error!r}")

    def _capture_and_recognize_window(self, hwnd: int, rect: tuple[int, int, int, int]) -> None:
        try:
            self.source_image, _capture_note = self._capture_selected_game_window(hwnd, rect)
            self.set_source_for_location(self.source_image, hwnd)
            self.recognize()
        except Exception as error:
            self._show_error(f"快捷截图失败：{error!r}")

    def toggle_auto(self) -> None:
        if self.auto_var.get():
            self.status_var.set("实时识别已开启：首次定位任务区域，后续仅识别变化的小区域。")
            self._auto_tick()
        else:
            self.status_var.set("实时识别已关闭；仍可使用 Ctrl+Alt+G 快捷识别。")

    def _auto_tick(self) -> None:
        if not self.auto_var.get():
            return
        self.quick_capture_and_recognize()
        self.after(int(DEFAULT_AUTO_INTERVAL_SECONDS * 1000), self._auto_tick)

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
        self.quick_capture_and_recognize()
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

    def recognize(self) -> None:
        if self.ocr_in_progress:
            return
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
        else:
            self.status_var.set("正在定位任务区域并识别中文文字，首次运行会下载 OCR 模型，请稍候……")
        threading.Thread(
            target=self._recognize_worker,
            args=(image, locate_from_source, window_handle, cached_region),
            daemon=True,
        ).start()

    def _recognize_worker(
        self,
        image: Image.Image,
        locate_from_source: bool,
        window_handle: int | None,
        cached_region: tuple[float, float, float, float] | None,
    ) -> None:
        try:
            from paddleocr import PaddleOCR
            import numpy as np

            if self.ocr_engine is None:
                self.ocr_engine = PaddleOCR(lang="ch", use_angle_cls=False, show_log=False)
            full_window_image = image.copy() if locate_from_source and window_handle is not None else None
            location_note = "手动选择的区域"
            used_cached_region = cached_region is not None
            region_for_cache: tuple[float, float, float, float] | None = None
            if cached_region:
                image = self._crop_relative_region(image, cached_region)
                location_note = "缓存定位：任务追踪区域"
            elif locate_from_source:
                full_image = self._limit_ocr_size(image)
                full_entries = self._ocr_entries(np.array(full_image))
                image, location_note, crop_box = locate_task_panel(full_image, full_entries)
                region_for_cache = self._relative_region(crop_box, full_image.size)

            signature = self._task_signature(image)
            if used_cached_region and self.last_task_signature is not None:
                if self._signature_difference(signature, self.last_task_signature) < UNCHANGED_FRAME_THRESHOLD:
                    self.after(0, self._show_unchanged_frame)
                    return

            lines, final_crop = self._recognize_task_crop(image, np)

            # A misleading full-window anchor can select player names or scenery.
            # Fall back before presenting or caching that crop if it has no task text.
            if full_window_image is not None and not has_task_panel_context(lines):
                fallback_box = default_task_crop_box(full_window_image)
                fallback_image = full_window_image.crop(fallback_box)
                fallback_lines, fallback_crop = self._recognize_task_crop(fallback_image, np)
                if has_task_panel_context(fallback_lines) or extract_candidate(fallback_lines):
                    lines = fallback_lines
                    final_crop = fallback_crop
                    signature = self._task_signature(fallback_image)
                    location_note = "自动定位未找到任务文字，已使用右侧任务区域回退"
                    region_for_cache = self._relative_region(fallback_box, full_window_image.size)
            self.after(
                0,
                lambda: self._show_result(
                    lines, final_crop, location_note, window_handle, region_for_cache, signature
                ),
            )
        except ModuleNotFoundError:
            self.after(0, lambda: self._show_error("缺少 OCR 依赖。请先双击“安装国令助手依赖.cmd”，完成后重新启动程序。"))
        except Exception as error:
            logging.exception("OCR engine failed")
            # Python clears an exception variable after an ``except`` block. Keep
            # its rendered text for the Tk callback, which runs later.
            error_text = f"识别失败：{error!r}"
            self.after(0, lambda: self._show_error(error_text))

    def _recognize_task_crop(self, image: Image.Image, numpy_module: object) -> tuple[list[str], Image.Image]:
        """Run the task-area OCR pass, including a higher-contrast retry."""
        scaled = image.resize(
            (image.width * TASK_OCR_SCALE, image.height * TASK_OCR_SCALE), Image.Resampling.LANCZOS
        )
        enhanced = ImageEnhance.Sharpness(ImageEnhance.Contrast(ImageOps.grayscale(scaled)).enhance(2.2)).enhance(2.0)
        entries = self._ocr_entries(numpy_module.array(scaled))
        if not entries:
            entries = self._ocr_entries(numpy_module.array(enhanced.convert("RGB")))
        lines = [text for _box, text in entries]
        final_crop = scaled.resize(
            (scaled.width // TASK_OCR_SCALE, scaled.height // TASK_OCR_SCALE), Image.Resampling.LANCZOS
        )
        return lines, final_crop

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
        result = self.ocr_engine.ocr(image_array, cls=False)
        entries: list[tuple[list[list[float]], str]] = []
        for entry in result[0] or []:
            if not entry or not entry[1][0].strip():
                continue
            box = [[float(point[0]), float(point[1])] for point in entry[0]]
            entries.append((box, entry[1][0].strip()))
        return entries

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
    ) -> None:
        self.crop_image = crop_image
        self.locate_from_source = False
        preview = crop_image.copy()
        preview.thumbnail((400, 380))
        self.preview_image = ImageTk.PhotoImage(preview)
        self.preview_label.configure(image=self.preview_image, text="")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", "\n".join(lines) if lines else "未识别到文字，请重新框选更紧凑的任务要求区域。")
        candidate = correct_item_name(extract_candidate(lines))
        self.last_task_signature = signature
        if window_handle is not None and region_for_cache is not None:
            self.cached_task_window = window_handle
            self.cached_task_region = region_for_cache
        self.ocr_in_progress = False
        self.recognize_button.configure(state="normal")
        if candidate:
            self.item_var.set(candidate)
            self.last_valid_item = candidate
            self.copy_item(silent=True)
            self.status_var.set(f"{location_note}；已识别“{candidate}”并复制到剪贴板。")
        else:
            if self.last_valid_item:
                self.item_var.set(self.last_valid_item)
                self.status_var.set(f"{location_note}；未发现完整道具行，已保留上次结果“{self.last_valid_item}”。")
            else:
                self.status_var.set(f"{location_note}；未发现完整道具行，可能被游戏界面遮挡，请关闭界面后重试。")

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
