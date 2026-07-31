"""Windows window-flash monitoring used by the integrated alert dialog."""

from __future__ import annotations

import ctypes
import logging
import threading
import uuid
import winsound
from dataclasses import dataclass
from ctypes import wintypes
from queue import Full, Queue

import pywintypes
import win32con
import win32gui


EVENT_SYSTEM_ALERT = 0x0002
HSHELL_FLASH = 0x8006
WINEVENT_OUTOFCONTEXT = 0x0000
WM_SHELLHOOK = win32con.WM_USER + 0x3F


@dataclass(frozen=True)
class FlashEvent:
    hwnd: int
    title: str
    source: str


def window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd).strip()
    except (pywintypes.error, TypeError, ValueError):
        return ""


def list_visible_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def callback(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        title = window_title(hwnd)
        if title:
            windows.append((hwnd, title))

    win32gui.EnumWindows(callback, None)
    return sorted(windows, key=lambda item: item[1].lower())


def play_sound(mode: str, wav_path: str) -> bool:
    try:
        if mode == "wav":
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            return True
        if mode == "beep":
            winsound.Beep(880, 350)
            return True
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        return True
    except (OSError, RuntimeError):
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return True
        except (OSError, RuntimeError):
            return False


class FlashMonitor:
    """Receive ShellHook and WinEvent alert notifications on a worker thread."""

    def __init__(self, events: Queue[FlashEvent]) -> None:
        self.events = events
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._window: int | None = None
        self._error: str | None = None
        self._class_name = f"GuoLingZhuShouFlash_{uuid.uuid4().hex}"

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        if self.running:
            return
        self._ready.clear()
        self._stopped.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="flash-alert-monitor", daemon=True)
        self._thread.start()
        if not self._ready.wait(3):
            self._error = "闪烁监听线程启动超时"
            self.stop()
        elif self._error:
            self.stop()
            raise RuntimeError(self._error)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._window is not None

    def stop(self) -> None:
        window = self._window
        if window:
            try:
                win32gui.PostMessage(window, win32con.WM_CLOSE, 0, 0)
            except win32gui.error:
                pass
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None
        self._window = None

    def _emit(self, hwnd: int, source: str) -> None:
        title = window_title(hwnd)
        if title:
            try:
                self.events.put_nowait(FlashEvent(hwnd, title, source))
            except Full:
                # Windows can produce alert events faster than the UI can consume them.
                # A later flash event is still useful, but retaining an unlimited backlog is not.
                pass

    def _run(self) -> None:
        hook = None
        callback = None
        hwnd = None
        try:
            message_id = win32gui.RegisterWindowMessage("SHELLHOOK")

            def wndproc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
                if message == message_id and wparam == HSHELL_FLASH:
                    self._emit(int(lparam), "ShellHook")
                    return 0
                if message == win32con.WM_DESTROY:
                    win32gui.PostQuitMessage(0)
                return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = wndproc
            wc.lpszClassName = self._class_name
            wc.hInstance = win32gui.GetModuleHandle(None)
            try:
                win32gui.RegisterClass(wc)
            except win32gui.error:
                pass
            hwnd = win32gui.CreateWindow(
                self._class_name, self._class_name, 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
            )
            if not hwnd:
                raise RuntimeError("无法创建闪烁监听窗口")
            self._window = hwnd
            user32 = ctypes.windll.user32
            user32.RegisterShellHookWindow.argtypes = [wintypes.HWND]
            user32.RegisterShellHookWindow.restype = wintypes.BOOL
            if not user32.RegisterShellHookWindow(hwnd):
                raise RuntimeError("无法注册 Windows ShellHook")

            callback_type = ctypes.WINFUNCTYPE(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                wintypes.LONG,
                wintypes.LONG,
                wintypes.DWORD,
                wintypes.DWORD,
            )

            @callback_type
            def event_callback(_hook, event, event_hwnd, _object, _child, _thread, _time):
                if event == EVENT_SYSTEM_ALERT and event_hwnd:
                    self._emit(int(event_hwnd), "WinEventAlert")

            callback = event_callback
            user32.SetWinEventHook.argtypes = [
                wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, callback_type,
                wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ]
            user32.SetWinEventHook.restype = wintypes.HANDLE
            hook = user32.SetWinEventHook(
                EVENT_SYSTEM_ALERT, EVENT_SYSTEM_ALERT, None, callback, 0, 0, WINEVENT_OUTOFCONTEXT
            )
            self._ready.set()
            win32gui.PumpMessages()
        except Exception as error:  # pragma: no cover - Windows API dependent
            self._error = str(error)
            self._ready.set()
            logging.warning("Window flash monitor failed", exc_info=True)
        finally:
            if hook:
                try:
                    ctypes.windll.user32.UnhookWinEvent(hook)
                except OSError:
                    pass
            if hwnd:
                try:
                    ctypes.windll.user32.DeregisterShellHookWindow(hwnd)
                except OSError:
                    pass
                try:
                    if win32gui.IsWindow(hwnd):
                        win32gui.DestroyWindow(hwnd)
                except win32gui.error:
                    pass
            self._stopped.set()
            self._window = None
            try:
                win32gui.UnregisterClass(self._class_name, win32gui.GetModuleHandle(None))
            except win32gui.error:
                pass


def matches_event(
    event: FlashEvent, title_filter: str, selected_hwnd: int | None, target_mode: str = "window"
) -> bool:
    if target_mode == "keyword":
        keywords = [keyword.strip().casefold() for keyword in title_filter.split("|") if keyword.strip()]
        return bool(keywords) and any(keyword in event.title.casefold() for keyword in keywords)
    return selected_hwnd is not None and event.hwnd == selected_hwnd
