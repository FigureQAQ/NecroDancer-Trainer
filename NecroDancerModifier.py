from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageTk


SOURCE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
MOD_SOURCE_DIR = RESOURCE_DIR / "mod"
STEAM_APP_ID = "247080"
APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
SAVE_DIR = APPDATA / "NecroDancer"
BACKUP_DIR = SAVE_DIR / "ModifierBackups"
MOD_DIR = LOCALAPPDATA / "NecroDancer" / "mods" / "NecroDancerTrainer"
MOD_CONFIG = MOD_DIR / "config.txt"
MOD_VERSION = json.loads((MOD_SOURCE_DIR / "mod.json").read_text(encoding="utf-8"))["version"]
PROCESS_NAMES = {"necrodancer.exe", "necrodancer64.exe"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
TH32CS_SNAPPROCESS = 0x00000002
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_MINIMIZE = 6
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_NUMPAD = {
    0: 0x60,
    1: 0x61,
    2: 0x62,
    3: 0x63,
    4: 0x64,
    5: 0x65,
    6: 0x66,
    7: 0x67,
    8: 0x68,
    9: 0x69,
}


def enable_process_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = wintypes.BOOL
        if set_context(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass

    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass

    try:
        ctypes.WinDLL("user32", use_last_error=True).SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


class GameRunningError(OSError):
    pass


def backup_and_replace_save(
    path: Path,
    original: bytes,
    updated: str,
    backup_tag: str,
    before_replace=None,
) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_prefix = f"{path.name}.{backup_tag}_"
    for existing in BACKUP_DIR.glob(f"{backup_prefix}*.bak"):
        try:
            if existing.read_bytes() == original:
                break
        except OSError:
            continue
    else:
        existing = None
    if existing is not None:
        backup = existing
    else:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        backup_base = f"{backup_prefix}{timestamp}"
        suffix = 0
        while True:
            extra = "" if suffix == 0 else f"_{suffix}"
            backup = BACKUP_DIR / f"{backup_base}{extra}.bak"
            try:
                with backup.open("xb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
                break
            except FileExistsError:
                suffix += 1
            except OSError:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(updated.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return backup


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


class ProcessFinder:
    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        ]
        self.kernel32.Process32FirstW.restype = wintypes.BOOL
        self.kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        ]
        self.kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.GetLastError.restype = wintypes.DWORD

    def is_running(self) -> bool:
        """Fail closed when the process list cannot be checked safely."""
        snapshot = self.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (0, ctypes.c_void_p(-1).value):
            return True
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return self.kernel32.GetLastError() != 18  # ERROR_NO_MORE_FILES
            while True:
                if entry.szExeFile.casefold() in PROCESS_NAMES:
                    return True
                if not self.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    return self.kernel32.GetLastError() != 18
        finally:
            self.kernel32.CloseHandle(snapshot)

    def find(self) -> tuple[int, str, Path | None] | None:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (0, ctypes.c_void_p(-1).value):
            return None
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return None
            while True:
                if entry.szExeFile.casefold() in PROCESS_NAMES:
                    pid = int(entry.th32ProcessID)
                    path = self.path(pid)
                    # The process name is enough to protect saves from writes
                    # while the game is running, even if Windows denies path access.
                    return pid, entry.szExeFile, path
                if not self.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    return None
        finally:
            self.kernel32.CloseHandle(snapshot)

    def path(self, pid: int) -> Path | None:
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return Path(buffer.value)
        finally:
            self.kernel32.CloseHandle(handle)

    def alive(self, pid: int) -> bool:
        handle = self.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(self.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
        finally:
            self.kernel32.CloseHandle(handle)


class Trainer(tk.Tk):
    def __init__(self):
        enable_process_dpi_awareness()
        super().__init__()
        self.dpi = self.get_window_dpi()
        self.scale_factor = self.dpi / 96
        self.tk.call("tk", "scaling", self.dpi / 72)
        self.title("NecroDancer Trainer")
        self.geometry(f"{self.ui_px(1000)}x{self.ui_px(780)}")
        self.minsize(self.ui_px(900), self.ui_px(650))
        self.configure(bg="#0e1624")
        self.font_family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.fonts = {
            "title": (self.font_family, 18, "bold"),
            "section": (self.font_family, 14, "bold"),
            "label": (self.font_family, 11, "bold"),
            "body": (self.font_family, 10),
            "small": (self.font_family, 9),
            "button": (self.font_family, 10, "bold"),
        }
        self.finder = ProcessFinder()
        self.game_dir = self.find_game_dir()
        self.status = tk.StringVar(value="未连接")
        self.process_text = tk.StringVar(value="游戏未运行")
        self.path_text = tk.StringVar(value=self.game_dir_text())
        self.diamond_value = tk.StringVar(value="")
        self.save_account = tk.StringVar(value="")
        self.save_account_invalidated = False
        self.diamond_account = ""
        self.save_account_combo = None
        self.max_health_value = tk.StringVar(value="3")
        self.coins_value = tk.StringVar(value="999999")
        self.coins_multiplier_value = tk.StringVar(value="2")
        self.vision_radius_value = tk.StringVar(value="0")
        self.level_value = tk.StringVar(value="1")
        self.item_value = tk.StringVar(value="weapon_longsword")
        self.character_value = tk.StringVar(value="Cadence")
        self.character_search = tk.StringVar(value="")
        self.item_window = None
        self.item_window_search = None
        self.item_window_search_entry = None
        self.item_window_category = None
        self.item_window_canvas = None
        self.item_window_grid = None
        self.item_window_status = None
        self.item_window_matches: list[tuple[str, str]] = []
        self.item_window_photos = {}
        self.item_tooltip = None
        self.item_tooltip_item = None
        self.item_icon_cache = {}
        self.item_metadata = {}
        self.command_id = 0
        self.coins_request_id = 0
        self.level_request_id = 0
        self.item_request_id = 0
        self.character_request_id = 0
        self.pending_requests = {"levelRequest": "0", "itemRequest": "", "characterRequest": ""}
        self.applied_values = {
            "maxHealthValue": "3", "coinsValue": "999999",
            "coinsMultiplier": "2", "visionRadius": "0",
        }
        self.hotkey_ids: dict[int, tuple[str, object]] = {}
        self.registered_hotkeys: list[int] = []
        self.search_popups: dict[int, tk.Toplevel] = {}
        self.search_lists: dict[int, tk.Listbox] = {}
        self.search_matches: dict[int, list[tuple[str, str]]] = {}
        self.item_aliases = self.load_item_aliases()
        self.character_aliases = self.load_character_aliases()
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
        ]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t,
        ]
        self.user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.variables = {
            "force_beat": tk.BooleanVar(value=False),
            "invincible": tk.BooleanVar(value=False),
            "one_hit": tk.BooleanVar(value=False),
            "double_damage": tk.BooleanVar(value=False),
            "free_shop": tk.BooleanVar(value=False),
            "dig_all": tk.BooleanVar(value=False),
            "unlock_all": tk.BooleanVar(value=False),
            "keep_coins_on_transition": tk.BooleanVar(value=True),
        }
        self.switches: dict[str, tk.Button] = {}
        self.item_catalog = self.load_item_catalog()
        self.character_catalog = self.load_character_catalog()
        self.ensure_mod_files()
        self.load_config()
        self.write_config()
        self.build_ui()
        self.after(100, self.configure_taskbar_window)
        self.register_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(800, self.poll)
        self.after(50, self.process_hotkeys)
        self.refresh_diamonds()

    def ui_px(self, value: int | float) -> int:
        return max(1, round(value * self.scale_factor))

    def get_window_dpi(self) -> int:
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_dpi = user32.GetDpiForWindow
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            return int(get_dpi(wintypes.HWND(self.winfo_id()))) or 96
        except (AttributeError, OSError):
            return 96

    def register_hotkeys(self):
        window = wintypes.HWND(self.winfo_id())
        definitions = [
            (1, "toggle", "force_beat", 0, 1),
            (2, "toggle", "invincible", 0, 2),
            (3, "toggle", "one_hit", 0, 3),
            (4, "toggle", "double_damage", 0, 4),
            (5, "toggle", "dig_all", 0, 5),
            (6, "toggle", "unlock_all", 0, 6),
            (7, "apply", "coins", MOD_CONTROL, 1),
            (8, "apply", "free_shop", MOD_CONTROL, 3),
            (9, "apply", "max_health", MOD_CONTROL, 4),
            (10, "apply", "level", MOD_CONTROL, 7),
            (11, "apply", "coins_multiplier", MOD_CONTROL, 8),
            (12, "apply", "diamonds", MOD_CONTROL, 2),
            (13, "apply", "vision_radius", MOD_CONTROL, 9),
        ]
        for hotkey_id, action, key, modifiers, number in definitions:
            modifiers |= MOD_NOREPEAT
            if self.user32.RegisterHotKey(window, hotkey_id, modifiers, VK_NUMPAD[number]):
                self.hotkey_ids[hotkey_id] = (action, key)
                self.registered_hotkeys.append(hotkey_id)
        missing = len(definitions) - len(self.registered_hotkeys)
        if missing:
            self.status.set(f"已启动，{missing} 个快捷键被其他程序占用")

    def process_hotkeys(self):
        if not self.winfo_exists():
            return
        message = MSG()
        window = wintypes.HWND(self.winfo_id())
        while self.user32.PeekMessageW(
            ctypes.byref(message), window, WM_HOTKEY, WM_HOTKEY, PM_REMOVE
        ):
            action = self.hotkey_ids.get(int(message.wParam))
            if not action:
                continue
            kind, key = action
            if kind == "toggle":
                self.toggle_feature(key)
            else:
                commands = {
                    "coins": self.apply_coins,
                    "free_shop": lambda: self.toggle_feature("free_shop"),
                    "max_health": self.apply_max_health,
                    "level": self.apply_level,
                    "coins_multiplier": self.apply_coins_multiplier,
                    "vision_radius": self.apply_vision_radius,
                    "diamonds": self.write_diamonds,
                }
                command = commands.get(key)
                if command:
                    command()
        self.after(50, self.process_hotkeys)

    def close(self):
        window = wintypes.HWND(self.winfo_id())
        for hotkey_id in self.registered_hotkeys:
            self.user32.UnregisterHotKey(window, hotkey_id)
        self.registered_hotkeys.clear()
        for timer in self.tk.splitlist(self.tk.call("after", "info")):
            self.after_cancel(timer)
        self.destroy()

    def minimize_window(self):
        self.update_idletasks()
        self.user32.ShowWindow(self.native_window_handle(), SW_MINIMIZE)

    def configure_taskbar_window(self):
        window = self.native_window_handle()
        extended_style = self.user32.GetWindowLongPtrW(window, GWL_EXSTYLE)
        extended_style = (extended_style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        self.user32.SetWindowLongPtrW(window, GWL_EXSTYLE, extended_style)
        self.user32.SetWindowPos(
            window,
            wintypes.HWND(0),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )

    def native_window_handle(self):
        frame = str(self.tk.call("wm", "frame", self._w))
        return wintypes.HWND(int(frame, 16) if frame.lower().startswith("0x") else int(frame))

    @staticmethod
    def is_game_dir(path: Path) -> bool:
        return (path / "Necrodancer64" / "Necrodancer.exe").is_file()

    def find_game_dir(self) -> Path | None:
        candidates = [SOURCE_DIR, Path.cwd()]
        try:
            import winreg

            for root, key_name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(root, key_name) as key:
                        install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                        candidates.append(Path(install_path))
                except OSError:
                    pass
        except ImportError:
            pass

        expanded = []
        for candidate in candidates:
            if candidate.is_file():
                candidate = candidate.parent
            expanded.append(candidate)
            expanded.append(candidate / "steamapps" / "common")

            library_file = candidate / "steamapps" / "libraryfolders.vdf"
            if library_file.is_file():
                try:
                    text = library_file.read_text(encoding="utf-8", errors="ignore")
                    for value in re.findall(r'"path"\s+"([^"]+)"', text):
                        expanded.append(Path(value) / "steamapps" / "common")
                except OSError:
                    pass

        for base in expanded:
            candidate = base / "Crypt of the NecroDancer"
            if self.is_game_dir(candidate):
                return candidate
            if self.is_game_dir(base):
                return base
        return None

    def game_dir_text(self) -> str:
        return str(self.game_dir) if self.game_dir else "未定位游戏目录"

    @staticmethod
    def load_item_aliases() -> dict[str, tuple[str, ...]]:
        return {
            "weapon_longsword": ("长剑",),
            "weapon_broadsword": ("阔剑", "大剑"),
            "weapon_dagger": ("匕首",),
            "weapon_dagger_phasing": ("穿透匕首", "相位匕首"),
            "weapon_spear": ("长矛", "矛"),
            "weapon_whip": ("鞭子",),
            "weapon_bow": ("弓",),
            "weapon_crossbow": ("弩",),
            "weapon_rifle": ("步枪",),
            "weapon_flail": ("连枷",),
            "armor_chainmail": ("锁子甲",),
            "armor_leather": ("皮甲",),
            "armor_glass": ("玻璃护甲",),
            "armor_heavyglass": ("重型玻璃护甲",),
            "armor_heavyplate": ("重型板甲",),
            "armor_platemail": ("板甲", "板金甲"),
            "armor_obsidian": ("黑曜石护甲", "黑曜石甲"),
            "feet_boots_speed": ("速度之靴", "速度鞋"),
            "feet_boots_winged": ("飞翼之靴", "翅膀鞋"),
            "head_helm": ("头盔",),
            "head_crown_of_greed": ("贪婪王冠", "贪婪皇冠"),
            "head_glass_jaw": ("玻璃下巴",),
            "head_monocle": ("单片眼镜", "显物眼镜"),
            "head_sunglasses": ("太阳镜", "墨镜"),
            "misc_map": ("地图",),
            "misc_potion": ("药水",),
            "misc_golden_key": ("金钥匙",),
            "misc_glass_key": ("玻璃钥匙",),
            "food_1": ("苹果",),
            "food_2": ("奶酪",),
            "food_3": ("鸡腿",),
            "food_4": ("火腿",),
            "bomb": ("炸弹",),
            "torch_1": ("火把", "普通火把"),
            "torch_2": ("明亮火把", "亮火把", "火把二"),
            "torch_3": ("发光火把", "亮晶晶火把", "火把三"),
            "torch_foresight": ("预见火把", "预言火把", "洞察火把"),
            "torch_glass": ("玻璃火把",),
            "torch_infernal": ("地狱火把", "炼狱火把"),
            "torch_obsidian": ("黑曜石火把", "黑曜石灯"),
            "torch_strength": ("力量火把",),
            "torch_walls": ("墙壁火把", "墙火把"),
            "spell_transform": ("变身法术",),
        }

    @staticmethod
    def load_character_aliases() -> dict[str, tuple[str, ...]]:
        return {
            "Cadence": ("卡登斯", "凯登斯"),
            "Aria": ("阿莉娅", "阿丽亚"),
            "Melody": ("梅洛迪", "旋律"),
            "Dorian": ("多里安",),
            "Eli": ("伊莱",),
            "Monk": ("僧侣",),
            "Coda": ("科达",),
            "Bolt": ("博尔特",),
            "Bard": ("吟游诗人", "诗人"),
            "Nocturna": ("诺克图娜", "夜曲"),
            "Diamond": ("钻石",),
            "Mary": ("玛丽",),
            "Tempo": ("坦波",),
            "Dove": ("鸽子",),
            "Reaper": ("死神",),
            "Chaunter": ("吟唱者",),
            "Klarinetta": ("克拉丽内塔",),
            "Suzu": ("铃",),
            "Kento": ("健斗",),
            "Beastmaster": ("野兽大师",),
            "Merlin": ("梅林",),
            "Goldman_Goldman": ("黄金人", "黄金商人"),
            "Coldsteel_Coldsteel": ("冷钢",),
        }

    def load_item_catalog(self) -> list[tuple[str, str]]:
        aliases = {
            internal: names[0] for internal, names in self.item_aliases.items()
        }
        if not self.game_dir:
            return [(key, aliases.get(key, key)) for key in aliases]
        xml_path = self.game_dir / "data" / "necrodancer.xml"
        result: list[tuple[str, str]] = []
        try:
            root = ET.parse(xml_path).getroot()
            items = root.find("items")
            if items is not None:
                for node in items:
                    item_id = node.tag
                    if not item_id or item_id.startswith("comment"):
                        continue
                    result.append((item_id, aliases.get(item_id, item_id)))
                    try:
                        image_width = int(node.attrib.get("imageW", "24"))
                        image_height = int(node.attrib.get("imageH", "24"))
                    except ValueError:
                        image_width, image_height = 24, 24
                    self.item_metadata[item_id] = {
                        "image": (node.text or "").strip(),
                        "slot": node.attrib.get("slot", ""),
                        "image_width": max(1, image_width),
                        "image_height": max(1, image_height),
                        "hint": node.attrib.get("hint", ""),
                    }
        except (OSError, ET.ParseError):
            result = []
        if not result:
            result = [(key, aliases.get(key, key)) for key in aliases]
        return sorted(set(result), key=lambda item: (item[1], item[0]))

    @staticmethod
    def item_name_in_chinese(item_id: str, display: str) -> str:
        if display and display != item_id:
            return display
        known = {
            "addchest_black": "黑色宝箱",
            "addchest_red": "红色宝箱",
            "addchest_white": "紫色宝箱",
            "armor_gi": "空手道服",
            "armor_heavyglass": "重型玻璃护甲",
            "armor_heavyplate": "重型板甲",
            "armor_platemail_dorian": "多里安的板甲",
            "armor_quartz": "石英护甲",
            "bag_holding": "储物袋",
            "bomb_3": "炸弹×3",
            "bomb_grenade": "手雷",
            "charm_bomb": "炸弹护符",
            "charm_frost": "寒冰护符",
            "charm_gluttony": "暴食护符",
            "charm_grenade": "手雷护符",
            "charm_luck": "幸运护符",
            "charm_nazar": "护身符",
            "charm_protection": "防护护符",
            "charm_risk": "风险护符",
            "charm_strength": "力量护符",
            "coins_x15": "金币倍率 +0.5",
            "coins_x2": "金币倍率 +1",
            "double_heart_transplant": "双重心脏移植",
            "familiar_dove": "鸽子使魔",
            "familiar_ice_spirit": "冰灵使魔",
            "familiar_rat": "老鼠使魔",
            "familiar_shield": "护盾使魔",
            "familiar_shopkeeper": "店主使魔",
            "feet_ballet_shoes": "芭蕾舞鞋",
            "feet_boots_explorers": "探险靴",
            "feet_boots_leaping": "跳跃靴",
            "feet_boots_lead": "铅靴",
            "feet_boots_lunging": "突进靴",
            "feet_boots_pain": "痛苦之靴",
            "feet_boots_speed": "速度之靴",
            "feet_boots_strength": "力量之靴",
            "feet_boots_winged": "飞翼之靴",
            "feet_glass_slippers": "玻璃舞鞋",
            "food_carrot": "胡萝卜",
            "food_cookies": "饼干",
            "food_magic_1": "魔法苹果",
            "food_magic_2": "魔法奶酪",
            "food_magic_3": "魔法鸡腿",
            "food_magic_4": "魔法火腿",
            "food_magic_carrot": "魔法胡萝卜",
            "food_magic_cookies": "魔法饼干",
            "head_blast_helm": "爆炸头盔",
            "head_circlet_telepathy": "心灵感应头环",
            "head_crown_of_greed": "贪婪王冠",
            "head_crown_of_thorns": "荆棘王冠",
            "head_crown_of_teleportation": "传送王冠",
            "head_glass_jaw": "玻璃下巴",
            "head_helm": "头盔",
            "head_miners_cap": "矿工帽",
            "head_ninja_mask": "忍者面罩",
            "head_sonar": "声呐",
            "head_spiked_ears": "尖刺耳罩",
            "lord_crown": "领主王冠",
            "misc_golden_key2": "金钥匙",
            "misc_golden_key3": "金钥匙",
            "misc_heart_container": "心之容器",
            "misc_heart_container2": "双重心之容器",
            "misc_heart_container_cursed": "诅咒心之容器",
            "misc_heart_container_cursed2": "双重诅咒心之容器",
            "misc_heart_container_empty": "空心容器",
            "misc_heart_container_empty2": "双重空心容器",
            "misc_monkey_paw": "猴爪",
            "ring_becoming": "蜕变戒指",
            "ring_charisma": "魅力戒指",
            "ring_courage": "勇气戒指",
            "ring_frost": "寒冰戒指",
            "ring_gold": "黄金戒指",
            "ring_luck": "幸运戒指",
            "ring_mana": "法力戒指",
            "ring_might": "强力戒指",
            "ring_pain": "痛苦戒指",
            "ring_peace": "和平戒指",
            "ring_phasing": "穿墙戒指",
            "ring_piercing": "穿刺戒指",
            "ring_protection": "防护戒指",
            "ring_regeneration": "再生戒指",
            "ring_shadows": "暗影戒指",
            "ring_shielding": "护盾戒指",
            "ring_war": "战争戒指",
            "ring_wonder": "奇迹戒指",
            "scroll_earthquake": "地震卷轴",
            "scroll_enchant_weapon": "附魔卷轴",
            "scroll_fear": "恐惧卷轴",
            "scroll_fireball": "火球卷轴",
            "scroll_freeze_enemies": "冰冻卷轴",
            "scroll_gigantism": "巨人卷轴",
            "scroll_need": "所需卷轴",
            "scroll_pulse": "脉冲卷轴",
            "scroll_riches": "财富卷轴",
            "scroll_shield": "护盾卷轴",
            "scroll_transmute": "转化卷轴",
            "shovel_battle": "战斗铲",
            "shovel_blood": "血铲",
            "shovel_courage": "勇气铲",
            "shovel_crystal": "水晶铲",
            "shovel_glass": "玻璃铲",
            "shovel_obsidian": "黑曜石铲",
            "shovel_shard": "玻璃碎片",
            "shovel_strength": "力量铲",
            "shovel_titanium": "钛铲",
            "spell_bomb": "炸弹法术",
            "spell_charm": "魅惑法术",
            "spell_earth": "大地法术",
            "spell_fireball": "火球法术",
            "spell_freeze_enemies": "冰冻法术",
            "spell_heal": "治疗法术",
            "spell_pulse": "脉冲法术",
            "spell_shield": "护盾法术",
            "spell_transform": "变身法术",
            "spell_transmute": "转化法术",
            "tome_earth": "大地法典",
            "tome_fireball": "火球法典",
            "tome_freeze": "冰冻法典",
            "tome_pulse": "脉冲法典",
            "tome_shield": "护盾法典",
            "tome_transmute": "转化法典",
            "torch_foresight": "预见火把",
            "torch_walls": "墙壁火把",
            "war_drum": "战鼓",
            "weapon_blunderbuss": "短铳",
            "weapon_cat": "九尾鞭",
            "weapon_dagger_electric": "电击匕首",
            "weapon_dagger_frost": "寒冰匕首",
            "weapon_dagger_jeweled": "宝石匕首",
            "weapon_dagger_shard": "玻璃碎片",
            "weapon_eli": "伊莱的武器",
            "weapon_flower": "花朵",
        }
        if item_id in known:
            return known[item_id]
        if item_id.startswith("resource_coin"):
            return "金币堆"
        if item_id.startswith("resource_diamond"):
            return "钻石"
        if item_id.startswith("resource_hoard"):
            return "金币宝藏"
        if item_id.startswith("perm_heart"):
            return "永久心之容器"
        words = {
            "addchest": "额外宝箱", "all": "全部", "apple": "苹果",
            "armor": "护甲", "axe": "斧", "backpack": "背包", "bag": "袋",
            "ballet": "芭蕾", "battle": "战斗", "becoming": "变身",
            "black": "黑色", "blood": "血", "bomb": "炸弹",
            "blunderbuss": "短铳", "boots": "靴", "bow": "弓",
            "broadsword": "阔剑", "carrot": "胡萝卜", "cat": "九尾鞭",
            "chainmail": "锁子甲", "charm": "护符", "charisma": "魅力",
            "cheese": "奶酪", "coin": "金币", "compass": "指南针",
            "container": "容器", "cookies": "饼干", "coupon": "优惠券",
            "courage": "勇气", "crystal": "水晶", "crown": "王冠",
            "crossbow": "弩", "cursed": "诅咒", "cutlass": "弯刀",
            "dagger": "匕首", "diamond": "钻石", "dorian": "多里安",
            "dove": "鸽子", "drum": "鼓", "drumstick": "鸡腿",
            "double": "双倍", "earth": "大地", "earthquake": "地震",
            "electric": "雷电", "eli": "伊莱", "empty": "空",
            "enchant": "附魔", "explorers": "探险", "familiar": "使魔",
            "fangs": "尖牙", "fear": "恐惧", "feet": "",
            "fireball": "火球", "flail": "连枷", "flower": "花",
            "food": "食物", "free": "免费", "freeze": "冰冻",
            "frost": "寒冰", "gi": "空手道服", "gigantism": "巨人",
            "glass": "玻璃", "gluttony": "暴食", "gold": "黄金",
            "golden": "黄金", "greaves": "护腿", "grenade": "手雷",
            "ham": "火腿", "harp": "竖琴", "head": "", "heal": "治疗",
            "heart": "心脏", "heavy": "重型", "hoard": "宝藏",
            "holster": "枪套", "holy": "圣水", "ice": "冰",
            "infernal": "地狱", "jeweled": "宝石", "key": "钥匙",
            "lead": "铅", "leather": "皮革", "leaping": "跳跃",
            "longsword": "长剑", "luck": "幸运", "lunging": "突进",
            "lute": "鲁特琴", "magic": "魔法", "magnet": "磁铁",
            "map": "地图", "mana": "法力", "mary": "玛丽",
            "melody": "梅洛迪", "miner": "矿工", "might": "强力",
            "misc": "", "monkey": "猴子", "monocle": "单片眼镜",
            "nazar": "护符", "need": "所需", "obsidian": "黑曜石",
            "pain": "痛苦", "peace": "和平", "perm": "永久",
            "phasing": "穿透", "pickaxe": "镐", "piercing": "穿刺",
            "plate": "板甲", "platemail": "板甲", "protection": "防护",
            "potion": "药水", "pulse": "脉冲", "quartz": "石英",
            "rapier": "刺剑", "rat": "老鼠", "red": "红色",
            "regeneration": "再生", "resource": "资源", "riches": "财富",
            "ring": "戒指", "risk": "风险", "rifle": "步枪",
            "scroll": "卷轴", "shard": "碎片", "shadows": "暗影",
            "shield": "盾牌", "shielding": "护盾", "shopkeeper": "店主",
            "shovel": "铲", "spear": "长矛", "spell": "法术",
            "staff": "法杖", "sonar": "声呐", "strength": "力量",
            "sun": "太阳", "sunglasses": "太阳镜", "telepathy": "心灵感应",
            "teleportation": "传送", "thorns": "荆棘", "titanium": "钛",
            "tome": "法典", "torch": "火把", "transmute": "转化",
            "transplant": "移植", "throwing": "投掷", "weapon": "",
            "war": "战争", "warhammer": "战锤", "whip": "鞭",
            "white": "紫色", "winged": "飞翼", "wonder": "奇迹", "x": "倍",
            "1": "一", "2": "二", "3": "三", "4": "四",
            "5": "五", "6": "六", "7": "七", "8": "八",
            "9": "九", "10": "十", "15": "十五", "50": "五十",
            "100": "一百", "150": "一百五十",
        }
        parts = [words[token] for token in item_id.casefold().split("_") if token in words]
        name = "".join(parts).strip()
        if name:
            return name
        category = item_id.split("_", 1)[0]
        return {
            "weapon": "武器", "armor": "护甲", "torch": "火把",
            "shovel": "铲子", "spell": "法术", "food": "食物",
        }.get(category, "游戏道具")

    @staticmethod
    def item_description_in_chinese(item_id: str, metadata: dict) -> str:
        raw_hint = metadata.get("hint", "")
        match = re.search(r"\|\d+\|([^|]*)\|", raw_hint)
        hint = match.group(1).strip().upper() if match else ""
        details = {
            "addchest_black": "本局额外增加 1 个黑色宝箱。",
            "addchest_red": "本局额外增加 1 个红色宝箱。",
            "addchest_white": "本局额外增加 1 个紫色宝箱。",
            "armor_chainmail": "护甲装备：防御力 +1，可将每次受到的伤害降低 1 点。",
            "armor_heavyglass": "重型玻璃护甲：抵挡最多 3 次伤害后破碎；重型属性可抵抗部分击退。",
            "armor_heavyplate": "重型板甲：防御力 +2；重型属性可抵抗部分击退。",
            "armor_leather": "皮甲：防御力 +0.5。",
            "armor_platemail": "板甲：防御力 +1.5。",
            "armor_platemail_dorian": "多里安的板甲：防御力 +1.5。",
            "armor_gi": "空手道服：武器基础伤害翻倍；受到的伤害额外增加半颗心。",
            "armor_glass": "玻璃护甲：完全抵挡一次伤害，随后破碎。",
            "armor_obsidian": "黑曜石护甲：防御力随金币连击倍率在 +0.5、+1、+1.5 之间提升。",
            "armor_quartz": "石英护甲：将单次受到的伤害限制为最多 1 点。",
            "feet_ballet_shoes": "芭蕾舞鞋：漏拍不会重置金币连击倍率；击败 1 个敌人即可将倍率升满。",
            "feet_boots_speed": "速度之靴：允许每个节拍行动两次；敌人速度也会加快。",
            "feet_boots_winged": "飞翼之靴：可切换飞行状态；免疫陷阱和地面危险，也不会触发导线伤害。",
            "feet_boots_explorers": "探险靴：免疫水、焦煤、黏液、熔岩和软泥等地面危险。",
            "feet_boots_lead": "铅靴：免疫冰面、软泥和尖刺陷阱，并获得重型属性，可抵抗击退。",
            "feet_boots_leaping": "跳跃靴：可切换为每次移动跳过 1 格；同时免疫水和焦油。",
            "feet_boots_lunging": "突进靴：可切换为最多跨越 3 格；撞向敌人或物体时造成 4 点伤害并击退。",
            "feet_boots_pain": "痛苦之靴：朝目标移动时造成 3 点伤害；拾取时损失 1.5 颗心，护甲无法抵消。",
            "feet_boots_strength": "力量之靴：攻击力 +1。",
            "feet_glass_slippers": "玻璃舞鞋：免疫多种地面危险；受到伤害后会破碎。",
            "feet_greaves": "护腿：防御力 +0.5。",
            "head_crown_of_thorns": "荆棘王冠：每击败 10 个敌人恢复半颗心；拾取时损失半颗心。",
            "head_crown_of_greed": "贪婪王冠：拾取金币翻倍，但每拍流失 1 枚金币。",
            "head_crown_of_teleportation": "传送王冠：抵挡一次伤害后破碎，并将你传送至商店或随机位置。",
            "head_circlet_telepathy": "心灵感应头环：显示本层所有敌人、NPC 和隐藏幽灵。",
            "head_miners_cap": "矿工帽：移动时自动挖掘周围墙壁并提高挖掘等级；挖开墙壁时会进入该格。",
            "head_monocle": "单片眼镜：显示本层道具、容器内物品，以及藏有金币或钻石的墙壁。",
            "head_ninja_mask": "忍者面罩：降低被敌人发现的机会。",
            "head_helm": "头盔：防御力 +0.5。",
            "head_glass_jaw": "玻璃下巴：攻击伤害翻倍；受到任何伤害都会立即死亡，并使头盔破碎。",
            "head_blast_helm": "爆炸头盔：免疫炸弹伤害，并增加 3 枚炸弹。",
            "head_spiked_ears": "尖刺耳罩：攻击力 +2；漏拍时会损坏。",
            "head_sunglasses": "太阳镜：攻击力 +1，但视野会变得异常。",
            "head_sonar": "声呐：显示本层所有敌人和 NPC。",
            "hud_backpack": "背包：可额外携带 1 件消耗品。",
            "holster": "枪套：允许额外携带并切换第二把武器。",
            "bag_holding": "储物袋：可携带任意数量的消耗品。",
            "misc_compass": "指南针：显示指向本层出口的方向。",
            "misc_coupon": "优惠券：降低商店价格。",
            "misc_golden_key": "金钥匙：可开启普通牢笼。",
            "misc_golden_key2": "金钥匙：可开启带金锁的牢笼。",
            "misc_golden_key3": "金钥匙：可开启普通牢笼。",
            "misc_glass_key": "玻璃钥匙：可开启带玻璃锁的牢笼。",
            "misc_heart_container_cursed": "诅咒心之容器：增加 1 格诅咒心上限。",
            "misc_heart_container_cursed2": "诅咒心之容器：增加 2 格诅咒心上限。",
            "misc_heart_container_empty": "空心容器：增加 1 格空心生命上限。",
            "misc_heart_container_empty2": "空心容器：增加 2 格空心生命上限。",
            "misc_key": "钥匙：可开启上锁的门和宝箱。",
            "misc_magnet": "磁铁：自动吸引附近的金币。",
            "misc_map": "地图：显示本层地牢地图。",
            "misc_monkey_paw": "猴爪：冻结猴类敌人。",
            "misc_potion": "药水：受到致命伤害时自动救你一次。",
            "misc_heart_container": "心之容器：增加 1 格最大生命。",
            "misc_heart_container2": "双重心之容器：增加 2 格最大生命。",
            "charm_bomb": "炸弹护符：使炸弹范围更大，并避免炸弹伤害自己。",
            "charm_frost": "寒冰护符：自动冻结靠近的敌人。",
            "charm_gluttony": "暴食护符：过量进食时获得一次额外奖励。",
            "charm_grenade": "手雷护符：允许投掷炸弹，爆炸伤害 +2。",
            "charm_luck": "幸运护符：提供蝙蝠防护，并改善箱子掉落。",
            "charm_nazar": "护身符：抵御幽灵类敌人。",
            "charm_protection": "防护护符：防御力 +0.5。",
            "charm_risk": "风险护符：生命值较低时攻击力 +2。",
            "charm_strength": "力量护符：攻击力 +1。",
            "cursed_potion": "诅咒药水：恢复全部生命，并将生命容器转化为诅咒心。",
            "holy_water": "圣水：对周围敌人造成 5 点伤害；每伤害一个敌人恢复 1 颗心，店主不受影响。",
            "lord_crown": "领主王冠：巨大化 50 拍，仅用于自定义地牢。",
            "war_drum": "战鼓：连续敲击会提高下一拍武器伤害，最多达到基础伤害的 3 倍。",
            "blood_drum": "血鼓：消耗半颗心；下一拍攻击造成致命伤害，挖掘可破坏任意墙壁。",
            "heart_transplant": "心脏移植：暂时大幅加快移动速度；效果结束时恢复原状。",
            "double_heart_transplant": "双重心脏移植：心脏移植效果持续更久。",
            "food_1": "苹果：恢复 1 颗心。",
            "food_2": "奶酪：恢复 2 颗心。",
            "food_3": "鸡腿：恢复 3 颗心。",
            "food_4": "火腿：恢复 4 颗心。",
            "food_carrot": "胡萝卜：恢复 1 颗心，并短暂显示地图。",
            "food_cookies": "饼干：恢复半颗心。",
            "food_magic_1": "魔法苹果：过量进食时可增加最大生命。",
            "food_magic_2": "魔法奶酪：过量进食时可增加最大生命。",
            "food_magic_3": "魔法鸡腿：过量进食时可增加最大生命。",
            "food_magic_4": "魔法火腿：过量进食时可增加最大生命。",
            "food_magic_carrot": "魔法胡萝卜：过量进食时可增加最大生命。",
            "food_magic_cookies": "魔法饼干：过量进食时可增加最大生命。",
            "bomb": "炸弹：放置后延迟爆炸，可破坏附近墙壁和物品。",
            "bomb_3": "炸弹：一次使用 3 枚炸弹。",
            "bomb_grenade": "手雷：可投掷并在落点爆炸。",
            "coins_x15": "金币倍率增加 0.5。",
            "coins_x2": "金币倍率增加 1。",
            "ring_courage": "勇气戒指：攻击力 +1；用武器击败敌人后朝攻击方向前进一格，并在该拍获得无敌。",
            "ring_war": "战争戒指：攻击力 +1，攻击附带击退；小头目会以更强形态出现。提示中的“更多敌人”并不准确。",
            "ring_peace": "和平戒指：装备时增加一格空心生命；新楼层每个房间减少一名敌人，小头目以较弱形态出现。",
            "ring_mana": "法力戒指：击杀敌人可更快恢复法术；可将支持的法术强化为高级版本。",
            "ring_shadows": "暗影戒指：商店物品免费，但视野缩小至 2 格；敌人、物品和陷阱显示为剪影。",
            "ring_might": "强力戒指：攻击力 +1。",
            "ring_charisma": "魅力戒指：金币商店价格减半，血液商店价格降低；典当物品可获得全额金币。",
            "ring_luck": "幸运戒指：提高物品品质、使蝙蝠不主动攻击，并保证幸运神龛成功；箱子和木桶会掉落更好的物品。",
            "ring_gold": "黄金戒指：敌人额外掉落金币；移动时自动拾取本层金币堆。",
            "ring_phasing": "穿墙戒指：允许穿过墙壁，但无法挖掘墙壁。",
            "ring_piercing": "穿刺戒指：普通攻击变为可穿透护甲的穿刺伤害。",
            "ring_regeneration": "再生戒指：进入下一层时恢复 1 颗心。",
            "ring_protection": "防护戒指：防御力 +0.5。",
            "ring_shielding": "护盾戒指：抵挡一次伤害后破碎，并施放高级护盾。",
            "ring_becoming": "蜕变戒指：受到任何伤害都会立即死亡；转化后会变为奇迹戒指。",
            "ring_wonder": "奇迹戒指：攻击力 +2、防御力 +0.5；击杀额外掉金币并加快法术恢复，进入下一层恢复 1 颗心，商店价格减半，并提供额外心之容器。",
            "ring_pain": "痛苦戒指：每次武器攻击都会伤害相邻敌人；拾取时损失半颗心。",
            "ring_frost": "寒冰戒指：攻击冻结敌人；攻击可将敌人冻结，冻结期间可一击击败。",
            "scroll_earthquake": "地震卷轴：清除周围大范围墙壁，并对范围内非飞行敌人造成 1 点伤害。",
            "scroll_fear": "恐惧卷轴：使附近敌人逃离。",
            "scroll_fireball": "火球卷轴：向水平方向发射火焰，伤害路径上的敌人。",
            "scroll_freeze_enemies": "冰冻卷轴：冻结本层敌人一段时间。",
            "scroll_gigantism": "巨人卷轴：暂时变大并提高挖掘能力。",
            "scroll_riches": "财富卷轴：获得额外金币。",
            "scroll_shield": "护盾卷轴：暂时进入无敌状态。",
            "scroll_enchant_weapon": "附魔卷轴：强化当前武器。",
            "scroll_need": "所需卷轴：根据当前情况给予有用物品。",
            "scroll_pulse": "脉冲卷轴：向斜向四周发动十字形攻击。",
            "scroll_transmute": "转化卷轴：随机转化身边未装备的物品。",
            "shovel_basic": "铲子：基础挖掘工具，挖掘等级 1。",
            "shovel_titanium": "钛铲：挖掘等级 2，可挖开更坚硬的墙。",
            "shovel_crystal": "水晶铲：挖掘等级 3，可挖开坚硬墙壁。",
            "shovel_obsidian": "黑曜石铲：挖掘等级随金币连击倍率提升。",
            "shovel_glass": "玻璃铲：挖掘等级 3；受到伤害后破碎并留下玻璃碎片。",
            "shovel_blood": "血铲：尝试挖掘更坚硬的墙会消耗半颗心，并挖开目标周围 3×3 区域；可挖任何墙。",
            "shovel_shard": "玻璃碎片：临时挖掘工具，挖掘等级 1。",
            "shovel_courage": "勇气铲：挖墙后进入被挖开的格子，并获得一拍无敌。",
            "shovel_strength": "力量铲：挖掘等级 2，可挖开 3×3 区域的墙，并使攻击力 +1。",
            "shovel_battle": "战斗铲：挖墙后下一拍攻击造成致命伤害；挖掘等级 2。",
            "pickaxe": "镐：挖掘等级 3；坚硬墙壁需要连续敲击多次才能挖开。",
            "torch_1": "火把：视野范围 +1 格，可隔墙照亮周围区域。",
            "torch_2": "明亮火把：视野范围 +2 格，可隔墙照亮周围区域。",
            "torch_3": "发光火把：视野范围 +3 格，可隔墙照亮周围区域。",
            "torch_foresight": "预见火把：视野范围 +1 格，并显示本层所有陷阱。",
            "torch_glass": "玻璃火把：视野范围 +3 格，显示敌人、NPC 和隐藏幽灵；受到伤害后破碎。",
            "torch_infernal": "地狱火把：视野范围 +1 格；挖掘带火把的墙时施放强化火球。",
            "torch_obsidian": "黑曜石火把：视野范围随金币连击倍率提升，最高 +3 格。",
            "torch_strength": "力量火把：视野范围 +1 格，攻击力 +1。",
            "torch_walls": "墙壁火把：视野范围 +1 格，并显示本层所有墙壁。",
            "torch_sonar": "声呐：周期性显示附近目标。",
            "weapon_golden_lute": "黄金鲁特琴：梅洛迪的专属武器，可在移动时攻击周围敌人。",
            "weapon_eli": "伊莱的专属武器：使用炸弹进行攻击。",
            "weapon_fangs": "尖牙：近距离攻击武器。",
            "weapon_flower": "花朵：特殊武器，攻击方式与角色及游戏模式有关。",
            "spell_earth": "大地法术：在周围升起土墙；强化后影响范围更大。",
            "spell_fireball": "火球法术：向左右水平发射火焰；击杀敌人可推进法术恢复。",
            "spell_pulse": "脉冲法术：向斜向四周发动范围攻击。",
            "spell_freeze_enemies": "冰冻法术：冻结敌人；强化后范围更大且持续更久。",
            "spell_heal": "治疗法术：恢复半颗心并在施法当拍提供无敌；强化后恢复 1 颗心。",
            "spell_bomb": "炸弹法术：在脚下放置炸弹；炸弹会延迟爆炸并摧毁附近墙壁。",
            "spell_shield": "护盾法术：暂时进入无敌状态；强化后持续更久。",
            "spell_transmute": "转化法术：随机转化附近地面物品；强化后可转化本层所有物品。",
            "spell_charm": "魅惑法术：使附近普通敌人暂时转而帮助你。",
            "spell_transform": "变身法术：切换蝙蝠形态；蝙蝠可飞越危险、查看敌人，但不能使用携带物品。",
            "tome_earth": "大地法典：使用后施放大地法术；可堆叠携带。",
            "tome_fireball": "火球法典：使用后施放火球法术；可堆叠携带。",
            "tome_freeze": "冰冻法典：使用后施放冰冻法术；可堆叠携带。",
            "tome_pulse": "脉冲法典：使用后施放脉冲法术；可堆叠携带。",
            "tome_shield": "护盾法典：使用后施放护盾法术；可堆叠携带。",
            "tome_transmute": "转化法典：使用后施放转化法术；可堆叠携带。",
        }
        if item_id in details:
            return details[item_id]

        if item_id.startswith("resource_coin"):
            amount = item_id.removeprefix("resource_coin")
            return f"金币堆：拾取后获得 {amount or '少量'} 枚金币；实际获得数受金币倍率影响。"
        if item_id.startswith("resource_diamond"):
            amount = item_id.removeprefix("resource_diamond")
            return f"钻石：拾取后获得 {amount or '1'} 颗钻石，可在大厅购买永久升级。"
        if item_id.startswith("resource_hoard"):
            return "金币堆：拾取后获得金币；实际获得数受金币倍率影响。"
        if item_id.startswith("perm_heart"):
            return "永久心之容器：增加 1 格最大生命上限。"
        if item_id.startswith("familiar_"):
            familiar_details = {
                "familiar_dove": "鸽子使魔：将附近敌人传送到其他位置。",
                "familiar_ice_spirit": "冰灵使魔：自动冻结附近敌人。",
                "familiar_shopkeeper": "店主使魔：拾取金币堆时恢复生命。",
                "familiar_shield": "护盾使魔：阻挡敌人靠近。",
                "familiar_rat": "老鼠使魔：自动攻击敌人，攻击可穿透护甲。",
            }
            return familiar_details.get(item_id, "使魔：会自动提供该使魔对应的辅助效果。")
        if item_id.startswith("tome_"):
            return "法典：使用后施放对应法术；可堆叠携带。"

        if item_id.startswith("weapon_"):
            weapon_hints = {
                "DAGGER, THROWABLE": "匕首：攻击正前方，可投掷造成直线伤害。",
                "GLASS SHARD": "玻璃碎片：作为临时武器造成 1 点伤害；也可用于挖掘。",
                "ARC DAMAGE": "电击匕首：攻击目标后，电弧会伤害附近敌人。",
                "PHASING ATTACK, 2 DMG": "穿透匕首：可穿过墙壁攻击，造成 2 点伤害。",
                "WIDER ATTACK": "鞭子：可攻击前方较宽范围。",
                "LONG ATTACK, THROWABLE": "长矛：可攻击前方较远的一格，也可以投掷。",
                "LUNGE FOR DOUBLE DAMAGE": "刺剑：向前突进攻击，突进时伤害翻倍。",
                "MOVE AND ATTACK": "九尾鞭：攻击时可向目标移动并同时攻击。",
                "LUNGE FOR WIDE ATTACK": "斧：向前突进并攻击一片区域。",
                "CIRCLE SPLASH": "竖琴：攻击时对周围区域造成溅射伤害。",
                "WIDE, LONG ATTACK": "战锤：可攻击前方较远且较宽的区域。",
                "LINE SPLASH": "法杖：攻击时对直线上的敌人造成溅射伤害。",
            }
            if hint in weapon_hints:
                return weapon_hints[hint]

            material = next(
                (
                    name for name in ("titanium", "obsidian", "golden", "blood", "glass")
                    if f"_{name}_" in f"_{item_id}_"
                ),
                "",
            )
            material_details = {
                "titanium": "钛制：每次攻击造成 2 点伤害。",
                "obsidian": "黑曜石：伤害随当前金币连击倍率在 1 至 3 点间变化。",
                "golden": "黄金：拾取金币后攻击力大幅提高，并使敌人额外掉落金币。",
                "blood": "鲜血：每击败 10 个敌人恢复半颗心；生命危急时可造成极高伤害。",
                "glass": "玻璃：造成 4 点伤害；受到伤害或投掷后破碎，并留下玻璃碎片。",
            }
            if material in material_details:
                base_hints = {
                    "weapon_broadsword": "阔剑可同时攻击前方一排敌人。",
                    "weapon_longsword": "长剑可攻击前方较远的一格。",
                    "weapon_rapier": "刺剑可突进攻击，突进时伤害翻倍。",
                    "weapon_spear": "长矛可攻击前方较远的一格，也可以投掷。",
                    "weapon_dagger": "匕首攻击正前方，可投掷造成直线伤害。",
                    "weapon_whip": "鞭子攻击前方较宽范围。",
                    "weapon_flail": "连枷可攻击周围敌人并将其击退。",
                    "weapon_bow": "弓可远距离攻击，但一次只能攻击一个目标。",
                    "weapon_crossbow": "弩可远距离穿透攻击，装填后效果更强。",
                    "weapon_blunderbuss": "短铳可扇形穿透攻击；射击后需要装填。",
                    "weapon_rifle": "步枪可远距离穿透攻击；射击后需要装填。",
                    "weapon_axe": "斧可向前突进并攻击一片区域。",
                    "weapon_harp": "竖琴攻击时可对周围区域造成溅射伤害。",
                    "weapon_warhammer": "战锤可攻击较远且较宽的区域。",
                    "weapon_staff": "法杖攻击时可对直线上的敌人造成溅射伤害。",
                    "weapon_cutlass": "弯刀可招架迎面而来的攻击。",
                    "weapon_cat": "九尾鞭可在攻击时移动，并优先攻击最近的敌人。",
                }
                base_id = item_id
                for prefix in ("titanium", "obsidian", "golden", "blood", "glass"):
                    base_id = base_id.replace(f"_{prefix}_", "_")
                base = base_hints.get(base_id, "武器攻击正前方的敌人。")
                return f"{base} {material_details[material]}"
        exact = {
            "+1 VISION RADIUS": "视野范围增加 1 格。",
            "+2 VISION RADIUS": "视野范围增加 2 格。",
            "+3 VISION RADIUS": "视野范围增加 3 格。",
            "SEE ALL ITEMS": "可查看本层所有道具。",
            "SEE ALL ENEMIES": "可查看本层所有敌人。",
            "MULTIPLIER-BASED LIGHT": "照明范围随金币倍率变化。",
            "MULTIPLIER-BASED DAMAGE": "攻击伤害随金币倍率变化。",
            "MULTIPLIER-BASED DEFENSE": "防御效果随金币倍率变化。",
            "MULTIPLIER-BASED DIG": "挖掘能力随金币倍率变化。",
            "DIG ANYTHING, BUT TAKE DAMAGE": "可以挖掘任何方块，但挖掘时会受到伤害。",
            "+1 VISION + TRAPSIGHT": "视野增加 1 格，并可看见陷阱。",
            "+1 VISION, +1 DAMAGE": "视野增加 1 格，攻击力增加 1。",
            "+1 VISION, REVEALS WALLS": "视野增加 1 格，并显示墙壁。",
            "MAGIC VISION, BREAKABLE": "提供魔法视野；受到伤害时会损坏。",
            "CONSUMES TORCHES ON WALLS": "撞上墙壁时会消耗火把。",
            "RESTORE 0.5 HEALTH": "恢复半颗心。",
            "RESTORE 1 HEALTH": "恢复 1 点生命。",
            "RESTORE 2 HEALTH": "恢复 2 点生命。",
            "RESTORE 3 HEALTH": "恢复 3 点生命。",
            "RESTORE 4 HEALTH": "恢复 4 点生命。",
            "+1 HEART CONTAINER": "最大生命上限增加 1 格。",
            "+2 HEART CONTAINER": "最大生命上限增加 2 格。",
            "+1 DAMAGE": "攻击力增加 1。",
            "+2 DMG": "攻击力增加 2。",
            "+0.5 DEFENSE": "防御力增加 0.5。",
            "+1 DEFENSE": "防御力增加 1。",
            "+1.5 DEFENSE": "防御力增加 1.5。",
            "+2 DEFENSE, HEAVY": "防御力增加 2，但移动较迟缓。",
            "2 DAMAGE": "造成 2 点伤害。",
            "4 DAMAGE, BREAKABLE": "造成 4 点伤害；受到伤害后会损坏。",
            "5 DAMAGE": "造成 5 点伤害。",
            "COIN MULTIPLIER +0.5": "金币倍率增加 0.5。",
            "COIN MULTIPLIER +1": "金币倍率增加 1。",
            "DOUBLE TIME": "移动速度加倍。",
            "LEVITATION": "可以越过地面障碍。",
            "LONG JUMP": "可以向前跳跃较远距离。",
            "LONG JUMP ATTACK": "向前突进并攻击。",
            "IGNORE TERRAIN, BREAKABLE": "可以无视地形；受到伤害后会损坏。",
            "OPEN LOCKS": "可打开锁住的门或宝箱。",
            "OPEN CAGE": "可打开牢笼。",
            "OPEN CAGE WITH GOLDEN LOCK": "可打开带金锁的牢笼。",
            "OPEN CAGE WITH GLASS LOCK": "可打开带玻璃锁的牢笼。",
            "REVEAL DUNGEON MINIMAP": "显示地牢地图。",
            "ARROW SHOWS EXIT": "显示指向出口的箭头。",
            "FREEZE ENEMIES": "冻结敌人。",
            "TEMPORARY SHIELD": "短时间内抵挡伤害。",
            "HORIZONTAL FIRE BLAST": "向水平方向发射火焰。",
            "X-SHAPED ATTACK": "向四个斜方向发动攻击。",
            "SCare ENEMIES AWAY": "使附近敌人逃离。",
            "SCARE ENEMIES AWAY": "使附近敌人逃离。",
            "PREVENTS DAMAGE 3 TIMES": "可以抵挡 3 次伤害。",
            "PREVENTS DAMAGE, THEN SHATTERS": "抵挡一次伤害后会破碎。",
            "TAKE AND DEAL DOUBLE DAMAGE": "受到和造成的伤害都会加倍。",
            "STAND STILL, KEEP MULTIPLIER": "静止不动时可保持金币倍率。",
            "HELPS WITH SOME TERRAIN": "可以更轻松地通过部分地形。",
            "HELPS WITH TRAPS AND ICE": "减少陷阱和冰面对移动的影响。",
            "DO DAMAGE AS YOU WALK": "移动时会对接触的敌人造成伤害。",
            "STAND STILL, INCREASE DAMAGE": "原地不动可蓄力提高攻击力。",
            "TAKES HEALTH, INCREASES DAMAGE": "消耗生命以提高攻击力。",
            "10 KILLS = HEAL": "每击败 10 个敌人恢复生命。",
            "DOUBLE COINS + LEAK COINS": "金币价值加倍，但会逐渐掉落金币。",
            "TELEPORT ON HIT": "受到攻击时会随机传送。",
            "BOMB IMMUNITY, +3 BOMBS": "免疫炸弹伤害，并增加 3 枚炸弹。",
            "INFINITE STORAGE": "提供无限道具储存空间。",
            "SAVES YOUR LIFE": "受到致命伤害时救你一命。",
            "BIG, SAFE BOMBS": "炸弹范围更大，且不会伤害自己。",
            "AUTOMATIC FREEZE": "自动冻结靠近的敌人。",
            "REWARDS OVEREATING, ONCE": "过量进食时获得一次额外奖励。",
            "HURL BOMBS WITH +2 DAMAGE": "投掷炸弹，爆炸伤害增加 2。",
            "WARDS OFF SPIRITS": "抵御幽灵类敌人。",
            "+2 DMG AT LOW HEALTH": "生命较低时攻击力增加 2。",
            "KILL INVULNERABILITY+DASH": "击败敌人后短暂无敌并向前冲刺。",
            "+1 DAMAGE, MORE ENEMIES": "攻击力增加 1，但敌人数量增加。",
            "+1 HEART, FEWER ENEMIES": "增加 1 格生命，并减少敌人数量。",
            "MORE POWERFUL SPELLS": "增强法术效果。",
            "CHEAPER SHOPS": "降低商店物品价格。",
            "MORE GOLD, AUTO GOLD PICKUP": "获得更多金币并自动拾取附近金币。",
            "AUTOMATIC SHIELD": "自动生成护盾抵挡攻击。",
            "DO DAMAGE AS YOU ATTACK": "每次攻击都会对敌人造成伤害。",
            "DAMAGE NON-FLYING ENEMIES": "对地面敌人造成伤害。",
            "ENCHANT WEAPON": "强化当前武器。",
            "GIVES WHAT YOU NEED": "根据当前情况给予所需物品。",
            "TRANSFORMS ALL NON-EQUIPPED ITEMS": "转化所有未装备的道具。",
            "+2 DIG": "挖掘等级增加 2。",
            "+1 DIG": "挖掘等级增加 1。",
            "SLOW, STRONG DIG": "挖掘速度较慢，但可以挖开坚硬方块。",
            "RAISE WALLS": "在周围升起土墙。",
            "TRANSFORM NEARBY ITEMS": "转化附近的道具。",
            "CHARM NEARBY ENEMIES": "魅惑附近的敌人。",
            "TOGGLE BAT FORM": "切换为蝙蝠形态。",
            "PROJECTILE": "发射远程投射物。",
            "TELEPORT ENEMIES": "将敌人传送到其他位置。",
            "GOLD PILES HEAL": "拾取金币堆时恢复生命。",
            "BLOCK ENEMIES": "阻挡敌人靠近。",
            "DO 1 DMG, PIERCING": "造成 1 点伤害并穿透敌人。",
            "CAST EARTH SPELL": "施放大地法术。",
            "CAST FIREBALL SPELL": "施放火球法术。",
            "CAST FREEZE SPELL": "施放冰冻法术。",
            "CAST PULSE SPELL": "施放脉冲法术。",
            "CAST SHIELD SPELL": "施放护盾法术。",
            "CAST TRANSMUTE SPELL": "施放转化法术。",
            "DOUBLE ALL DMG, FRAILTY": "造成双倍伤害，但自身也更脆弱。",
            "+2 DIG, BREAKABLE": "挖掘等级增加 2；受到伤害后会损坏。",
            "+1 DIG, INF DMG AFTER DIG": "挖掘等级增加 1；挖掘后攻击可造成致命伤害。",
            "+1 DIG, DIG DASH": "挖掘等级增加 1，并可冲刺挖掘。",
            "+1 DIG, MORE": "挖掘能力提高。",
            "FREEZE ENEMIES, KILL FROZEN ENEMIES": "冻结敌人；攻击被冻结的敌人可将其击败。",
            "FREEZE ENEMIES, KILL FROZEN ENEMIES, PIERCING": "冻结敌人；攻击被冻结的敌人可将其击败，且攻击可穿透。",
            "RANGED ATTACK, NEEDS RELOADING, PIERCING": "可远程穿透攻击，但需要装填。",
            "RANGED ATTACK, NEEDS LOADING, PIERCING": "可远程穿透攻击，但攻击前需要装填。",
            "CONICAL BLAST, NEEDS RELOADING, PIERCING": "向扇形范围发射穿透攻击，需要装填。",
            "HEALS EACH LEVEL": "每通过一层恢复生命。",
            "WEAPON PIERCES": "武器攻击可以穿透敌人。",
            "RANGED ATTACK": "可以进行远距离攻击。",
            "WIDE ATTACK": "攻击范围较宽。",
            "LONG ATTACK": "攻击距离较远。",
            "KNOCKBACK ATTACK": "攻击会击退敌人。",
            "PARRY ATTACK": "可招架迎面而来的攻击。",
            "RICHES": "获得额外金币。",
            "MAGNETISM": "自动吸引附近金币。",
            "SHOPPING SPREE": "商店商品价格降低。",
            "STEALTHY": "降低被敌人发现的机会。",
            "FLOWER": "特殊武器；游戏未提供更详细的文字说明。",
        }
        if hint in exact:
            return exact[hint]
        if "HIGH DMG AFTER GOLD PICKUP" in hint:
            return "拾取金币后攻击力提高。"
        if "KILL TO HEAL" in hint:
            return "击败敌人可恢复生命；低生命时攻击力提高。"
        if "HEAL" in hint and "KILL" in hint:
            return "击败敌人后恢复生命。"
        if "BREAKABLE" in hint:
            return "攻击力较高，但受到伤害后会损坏。"
        if "DAMAGE" in hint and "DMG" in hint:
            return "造成额外伤害。"
        if hint.startswith("+") and "CHEST PER RUN" in hint:
            count, chest = hint.split()[0][1:], hint.split()[1]
            color = {"BLACK": "黑色", "RED": "红色", "PURPLE": "紫色"}.get(chest, "")
            return f"本次游戏额外获得 {count} 个{color}宝箱。"
        if "OVEREAT FOR HEARTS" in hint:
            return "过量进食时可增加最大生命。"
        if "CURSED HEART CONTAINER" in hint:
            return "增加诅咒心容器；受到伤害时会影响诅咒心。"
        if "EMPTY HEART CONTAINER" in hint:
            return "增加空心容器上限。"
        if "FREEZE" in hint:
            return "可以冻结敌人。"
        if "PIERCING" in hint:
            return "攻击可以穿透敌人。"
        if "NEEDS RELOADING" in hint or "NEEDS LOADING" in hint:
            return "可进行远程攻击；攻击后需要装填。"
        if hint:
            return "游戏内效果说明暂未整理；可在游戏中查看该道具的提示。"
        return "游戏未提供文字效果说明。"

    @staticmethod
    def load_character_catalog() -> list[tuple[str, str]]:
        names = [
            ("Cadence", "卡登斯"),
            ("Aria", "阿莉娅"),
            ("Melody", "梅洛迪"),
            ("Dorian", "多里安"),
            ("Eli", "伊莱"),
            ("Monk", "僧侣"),
            ("Coda", "科达"),
            ("Bolt", "博尔特"),
            ("Bard", "吟游诗人"),
            ("Nocturna", "诺克图娜"),
            ("Diamond", "钻石"),
            ("Mary", "玛丽"),
            ("Tempo", "坦波"),
            ("Dove", "鸽子"),
            ("Reaper", "死神"),
            ("Chaunter", "吟唱者"),
            ("Klarinetta", "克拉丽内塔"),
            ("Suzu", "铃"),
            ("Kento", "健斗"),
            ("Beastmaster", "野兽大师"),
            ("Merlin", "梅林"),
            ("Goldman_Goldman", "黄金人"),
            ("Coldsteel_Coldsteel", "冷钢"),
        ]
        return names

    def catalog_aliases(self, catalog: list[tuple[str, str]]) -> dict[str, tuple[str, ...]]:
        return self.item_aliases if catalog is self.item_catalog else self.character_aliases

    def catalog_matches(self, query: str, catalog: list[tuple[str, str]]) -> list[tuple[str, str]]:
        folded = query.casefold()
        aliases = self.catalog_aliases(catalog)
        generated_names = catalog is self.item_catalog
        matches = []
        for internal, display in catalog:
            if not folded:
                matches.append((internal, display))
                continue
            generated = (
                self.item_name_in_chinese(internal, display)
                if generated_names else ""
            )
            if (
                folded in internal.casefold()
                or folded in display.casefold()
                or folded in generated.casefold()
                or any(folded in alias.casefold() for alias in aliases.get(internal, ()))
            ):
                matches.append((internal, display))
        return matches

    @staticmethod
    def item_category(item_id: str, metadata: dict) -> str:
        slot = metadata.get("slot", "")
        categories = {
            "weapon": "武器",
            "torch": "火把",
            "shovel": "铲子",
            "head": "头部装备",
            "armor": "护甲",
            "feet": "鞋子",
            "spell": "法术",
            "action": "道具",
        }
        if slot in categories:
            return categories[slot]
        prefix = item_id.split("_", 1)[0]
        return {
            "weapon": "武器",
            "torch": "火把",
            "shovel": "铲子",
            "head": "头部装备",
            "armor": "护甲",
            "feet": "鞋子",
            "spell": "法术",
        }.get(prefix, "道具")

    def item_icon(self, item_id: str, size: int):
        cache_key = (item_id, size)
        if cache_key in self.item_icon_cache:
            return self.item_icon_cache[cache_key]
        metadata = self.item_metadata.get(item_id, {})
        filename = metadata.get("image") or f"{item_id}.png"
        asset_root = self.game_dir / "data" / "items" if self.game_dir else None
        if asset_root is None or Path(filename).name != filename:
            return None
        path = asset_root / filename
        if not path.is_file():
            return None
        try:
            with Image.open(path) as source:
                frame_width = metadata.get("image_width", 24)
                frame_height = metadata.get("image_height", 24)
                frame = source.convert("RGBA").crop(
                    (0, 0, min(frame_width, source.width), min(frame_height, source.height))
                )
                ratio = min(size / frame.width, size / frame.height)
                frame = frame.resize(
                    (max(1, round(frame.width * ratio)), max(1, round(frame.height * ratio))),
                    Image.Resampling.NEAREST,
                )
                canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                canvas.alpha_composite(
                    frame,
                    ((size - frame.width) // 2, (size - frame.height) // 2),
                )
            photo = ImageTk.PhotoImage(canvas)
        except (OSError, ValueError):
            return None
        self.item_icon_cache[cache_key] = photo
        return photo

    def open_item_window(self):
        if self.item_window and self.item_window.winfo_exists():
            self.item_window.deiconify()
            self.item_window.lift()
            self.item_window_search_entry.focus_set()
            return

        bg = self.ui_colors["bg"]
        panel = "#252728"
        text = self.ui_colors["text"]
        muted = self.ui_colors["muted"]
        accent = self.ui_colors["accent"]
        line = self.ui_colors["line"]
        window = tk.Toplevel(self)
        self.item_window = window
        window.title("获取道具或武器")
        window.geometry(f"{self.ui_px(960)}x{self.ui_px(650)}")
        window.minsize(self.ui_px(780), self.ui_px(540))
        window.configure(bg=bg)
        window.transient(self)
        window.protocol("WM_DELETE_WINDOW", self.close_item_window)

        header = tk.Frame(window, bg=bg)
        header.pack(fill="x", padx=self.ui_px(14), pady=(self.ui_px(12), self.ui_px(8)))
        tk.Label(
            header, text="获取道具", bg=bg, fg=text,
            font=self.ui_fonts["header"], anchor="w",
        ).pack(side="left", padx=(0, self.ui_px(14)))
        self.item_window_search = tk.StringVar()
        search = tk.Entry(
            header, textvariable=self.item_window_search,
            bg=panel, fg=text, insertbackground=text, relief="flat", bd=0,
            font=self.ui_fonts["input"],
        )
        search.pack(side="left", fill="x", expand=True, ipady=self.ui_px(7))
        self.item_window_search_entry = search
        search.insert(0, "搜索中文名或道具 ID")
        search.configure(fg=muted)

        self.item_window_category = tk.StringVar(value="全部")
        body = tk.Frame(window, bg=bg)
        body.pack(fill="both", expand=True, padx=self.ui_px(14), pady=(0, self.ui_px(8)))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        categories = tk.Frame(body, bg=bg, width=self.ui_px(116))
        categories.grid(row=0, column=0, sticky="nsw", padx=(0, self.ui_px(10)))
        categories.grid_propagate(False)
        category_buttons = {}

        grid_panel = tk.Frame(body, bg=panel)
        grid_panel.grid(row=0, column=1, sticky="nsew")
        grid_panel.grid_columnconfigure(0, weight=1)
        grid_panel.grid_rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            grid_panel, bg=panel, highlightthickness=0, borderwidth=0,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(grid_panel, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        inner = tk.Frame(canvas, bg=panel)
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")
        self.item_window_canvas = canvas
        self.item_window_grid = inner
        self.item_window_status = tk.StringVar(value="")
        grid_columns = [0]
        resize_job = [None]
        rendered_width = [0]
        tile_size = self.ui_px(70)
        tile_gap = self.ui_px(4)
        icon_size = self.ui_px(52)

        def update_scrollregion():
            if window.winfo_exists() and canvas.winfo_exists():
                canvas.configure(scrollregion=canvas.bbox("all"))

        def refresh(_event=None):
            if not window.winfo_exists() or not canvas.winfo_exists():
                return
            canvas_width = canvas.winfo_width()
            if canvas_width <= 1:
                return
            if search.get() == "搜索中文名或道具 ID":
                query = ""
            else:
                query = search.get().strip()
            category = self.item_window_category.get()
            matches = self.catalog_matches(query, self.item_catalog)
            self.item_window_matches = [
                (item_id, name) for item_id, name in matches
                if category == "全部"
                or self.item_category(item_id, self.item_metadata.get(item_id, {})) == category
            ]
            for child in inner.winfo_children():
                child.destroy()
            self.item_window_photos.clear()
            columns = max(1, (canvas_width - self.ui_px(8)) // (tile_size + tile_gap))
            for column in range(max(columns, grid_columns[0])):
                inner.grid_columnconfigure(column, minsize=0, uniform="")
            for column in range(columns):
                inner.grid_columnconfigure(column, minsize=tile_size, uniform="item_tiles")
            grid_columns[0] = columns
            for index, (item_id, _name) in enumerate(self.item_window_matches):
                row, column = divmod(index, columns)
                tile = tk.Frame(inner, bg=panel, width=tile_size, height=tile_size)
                tile.grid(row=row, column=column, padx=self.ui_px(2), pady=self.ui_px(2))
                tile.grid_propagate(False)
                icon = self.item_icon(item_id, icon_size)
                if icon:
                    self.item_window_photos[item_id] = icon
                button = tk.Button(
                    tile, image=icon, text="" if icon else "?",
                    bg="#353738", fg=text, activebackground=accent,
                    activeforeground="#171717", relief="flat", bd=0,
                    highlightthickness=0, cursor="hand2",
                    command=lambda current=item_id: self.request_item(current),
                )
                button.pack(fill="both", expand=True, padx=self.ui_px(3), pady=self.ui_px(3))
                button.bind(
                    "<Enter>",
                    lambda event, current=item_id: self.show_item_tooltip(
                        current, event.x_root, event.y_root,
                    ),
                )
                button.bind(
                    "<Motion>",
                    lambda event, current=item_id: self.show_item_tooltip(
                        current, event.x_root, event.y_root,
                    ),
                )
                button.bind("<Leave>", self.hide_item_tooltip)
                button.bind("<MouseWheel>", lambda event: canvas.yview_scroll(
                    -1 if event.delta > 0 else 1, "units",
                ))
            if not self.item_window_matches:
                tk.Label(
                    inner, text="没有匹配的道具", bg=panel, fg=muted,
                    font=self.ui_fonts["small"],
                ).grid(row=0, column=0, sticky="nw", padx=self.ui_px(14), pady=self.ui_px(14))
            canvas.itemconfigure(canvas_window, width=canvas_width)
            rendered_width[0] = canvas_width
            canvas.after_idle(update_scrollregion)
            canvas.yview_moveto(0)
            count_label.configure(text=f"{len(self.item_window_matches)} 项")

        def set_category(category):
            self.item_window_category.set(category)
            for name, button in category_buttons.items():
                selected = name == category
                button.configure(
                    bg="#3b3d3e" if selected else bg,
                    fg=accent if selected else muted,
                )
            refresh()

        for category in ("全部", "武器", "火把", "铲子", "头部装备", "护甲", "鞋子", "法术", "道具"):
            button = tk.Button(
                categories, text=category, anchor="w",
                padx=self.ui_px(10), pady=self.ui_px(7),
                bg=bg, fg=muted, activebackground="#3b3d3e",
                activeforeground=text, relief="flat", bd=0,
                cursor="hand2", font=self.ui_fonts["small"],
                command=lambda current=category: set_category(current),
            )
            button.pack(fill="x", pady=self.ui_px(1))
            category_buttons[category] = button
        count_label = tk.Label(
            header, text=f"{len(self.item_catalog)} 项", bg=bg, fg=muted,
            font=self.ui_fonts["small"], anchor="e", width=8,
        )
        count_label.pack(side="right", padx=(self.ui_px(10), 0))
        tk.Frame(window, bg=line, height=self.ui_px(1)).pack(
            fill="x", padx=self.ui_px(14), pady=(0, self.ui_px(8)),
        )
        tk.Label(
            window, textvariable=self.item_window_status, bg=bg, fg=muted,
            font=self.ui_fonts["small"], anchor="w",
        ).pack(fill="x", padx=self.ui_px(14), pady=(0, self.ui_px(10)))

        def focus_search(_event=None):
            if search.get() == "搜索中文名或道具 ID":
                search.delete(0, "end")
                search.configure(fg=text)

        def restore_placeholder(_event=None):
            if not search.get().strip():
                search.insert(0, "搜索中文名或道具 ID")
                search.configure(fg=muted)
                refresh()

        def resize_grid(event):
            if event.width <= 1 or event.width == rendered_width[0]:
                return
            if resize_job[0] is not None:
                canvas.after_cancel(resize_job[0])
            resize_job[0] = canvas.after(80, refresh)

        search.bind("<FocusIn>", focus_search)
        search.bind("<FocusOut>", restore_placeholder)
        search.bind("<KeyRelease>", refresh)
        search.bind(
            "<Return>",
            lambda _event: self.request_item(self.item_window_matches[0][0])
            if self.item_window_matches else None,
        )
        def close_on_escape(_event):
            self.close_item_window()
            return "break"

        window.bind("<Escape>", close_on_escape)
        canvas.bind("<Configure>", resize_grid)
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(
            -1 if event.delta > 0 else 1, "units",
        ))
        set_category("全部")
        refresh()
        search.focus_set()

    def item_tooltip_text(self, item_id: str) -> str:
        display = next(
            (name for candidate, name in self.item_window_matches if candidate == item_id),
            item_id,
        )
        name = self.item_name_in_chinese(item_id, display)
        metadata = self.item_metadata.get(item_id, {})
        category = self.item_category(item_id, metadata)
        description = self.item_description_in_chinese(item_id, metadata)
        return f"{name}  ·  {category}\n{description}\n內部 ID：{item_id}"

    def show_item_tooltip(self, item_id: str, x_root: int, y_root: int):
        if not self.item_window or not self.item_window.winfo_exists():
            return
        text = self.item_tooltip_text(item_id)
        if self.item_tooltip_item == (item_id, text) and self.item_tooltip:
            popup = self.item_tooltip
            popup.geometry(f"+{x_root + self.ui_px(14)}+{y_root + self.ui_px(18)}")
            return
        self.hide_item_tooltip()
        popup = tk.Toplevel(self.item_window)
        popup.wm_overrideredirect(True)
        popup.configure(bg="#111314")
        label = tk.Label(
            popup, text=text, justify="left", anchor="w",
            wraplength=self.ui_px(330),
            bg="#252728", fg=self.ui_colors["text"],
            padx=self.ui_px(10), pady=self.ui_px(8),
            font=self.ui_fonts["small"], relief="solid", bd=1,
        )
        label.pack()
        popup.update_idletasks()
        width, height = popup.winfo_reqwidth(), popup.winfo_reqheight()
        x = min(
            x_root + self.ui_px(14),
            self.winfo_screenwidth() - width - self.ui_px(8),
        )
        y = min(
            y_root + self.ui_px(18),
            self.winfo_screenheight() - height - self.ui_px(8),
        )
        popup.geometry(f"+{max(self.ui_px(8), x)}+{max(self.ui_px(8), y)}")
        self.item_tooltip = popup
        self.item_tooltip_item = (item_id, text)

    def hide_item_tooltip(self, _event=None):
        popup = self.item_tooltip
        self.item_tooltip = None
        self.item_tooltip_item = None
        if popup and popup.winfo_exists():
            popup.destroy()

    def close_item_window(self):
        self.hide_item_tooltip()
        window = self.item_window
        self.item_window = None
        self.item_window_canvas = None
        self.item_window_grid = None
        self.item_window_status = None
        self.item_window_search_entry = None
        self.item_window_photos.clear()
        if window and window.winfo_exists():
            window.destroy()

    def ensure_mod_files(self) -> None:
        MOD_DIR.mkdir(parents=True, exist_ok=True)
        for relative in ("mod.json", "NecroDancerTrainer.lua"):
            source = MOD_SOURCE_DIR / relative
            target = MOD_DIR / relative
            if not source.is_file():
                raise FileNotFoundError(f"缺少内置模组文件：{source}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.read_bytes() != source.read_bytes():
                shutil.copy2(source, target)
        if not MOD_CONFIG.is_file():
            default_config = MOD_SOURCE_DIR / "config.txt"
            if default_config.is_file():
                shutil.copy2(default_config, MOD_CONFIG)
            else:
                self.write_config()
        self.ensure_mod_enabled()

    def ensure_mod_enabled(self) -> None:
        # The game writes its in-memory options on exit. Repair only while stopped.
        if self.game_is_running():
            return
        save_root = APPDATA / "NecroDancer"
        option_files = [save_root / "SynchronyOptions.lua"]
        option_files.extend(save_root.glob("SynchronyUser_*.lua"))
        marker = '["modLoader.list"] = {'
        for options in option_files:
            if not options.is_file():
                continue
            try:
                original = options.read_bytes()
                text = original.decode("utf-8")
            except FileNotFoundError:
                continue
            except UnicodeError:
                continue
            existing = re.search(
                r'\{(?=[^{}]*\bname\s*=\s*"NecroDancerTrainer")[^{}]*\}',
                text,
            )
            if existing:
                entry = existing.group(0)
                version = re.search(r'\bversion\s*=\s*"([^"]+)"', entry)
                if version and version.group(1) == MOD_VERSION:
                    continue
                if version:
                    updated_entry = (
                        entry[:version.start(1)] + MOD_VERSION + entry[version.end(1):]
                    )
                else:
                    updated_entry = entry[:-1] + f',version="{MOD_VERSION}"' + entry[-1]
                updated = text[:existing.start()] + updated_entry + text[existing.end():]
            elif marker in text:
                updated = text.replace(
                    marker,
                    f'{marker}{{name="NecroDancerTrainer",package=false,version="{MOD_VERSION}"}},',
                    1,
                )
            else:
                continue
            backup_and_replace_save(
                options, original, updated, "before_trainer",
                before_replace=lambda: self.require_game_stopped(
                    "游戏已启动，已取消模组配置写入"
                ),
            )

    def game_is_running(self) -> bool:
        checker = getattr(self.finder, "is_running", None)
        return checker() if checker else bool(self.finder.find())

    def require_game_stopped(self, message: str) -> None:
        if self.game_is_running():
            raise GameRunningError(message)

    def load_config(self) -> None:
        values = {}
        try:
            for line in MOD_CONFIG.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    values[key.strip()] = value.strip()
        except OSError:
            values = {}
        for key in (
            "forceBeat",
            "skipMissedBeat",
            "invincible",
            "oneHit",
            "doubleDamage",
            "freeShop",
            "infiniteCoins",
            "digAll",
            "unlockAll",
            "keepCoinsOnTransition",
        ):
            ui_key = {
                "forceBeat": "force_beat",
                "skipMissedBeat": "force_beat",
                "oneHit": "one_hit",
                "doubleDamage": "double_damage",
                "freeShop": "free_shop",
                "infiniteCoins": "free_shop",
                "digAll": "dig_all",
                "unlockAll": "unlock_all",
                "keepCoinsOnTransition": "keep_coins_on_transition",
            }.get(key, key)
            if key in values:
                self.variables[ui_key].set(values[key] in ("1", "true"))
        self.max_health_value.set(values.get(
            "maxHealthValue", values.get("healthValue", self.max_health_value.get())
        ))
        self.coins_value.set(values.get("coinsValue", self.coins_value.get()))
        self.coins_multiplier_value.set(values.get(
            "coinsMultiplier", values.get("speed", self.coins_multiplier_value.get())
        ))
        self.vision_radius_value.set(values.get(
            "visionRadius", values.get(
                "visionMultiplier", values.get("vision", self.vision_radius_value.get())
            )
        ))
        self.level_value.set(values.get("levelRequest", self.level_value.get()))
        self.item_value.set(values.get("itemRequest") or self.item_value.get())
        self.character_value.set(values.get("characterRequest") or self.character_value.get())
        self.pending_requests = {
            key: values.get(key, default)
            for key, default in self.pending_requests.items()
        }
        numeric_fields = (
            ("maxHealthValue", self.max_health_value, 0, 999, True),
            ("coinsValue", self.coins_value, 0, 2_147_483_647, True),
            ("coinsMultiplier", self.coins_multiplier_value, 1, 100, False),
            ("visionRadius", self.vision_radius_value, 0, 100, True),
        )
        for key, variable, minimum, maximum, integer in numeric_fields:
            try:
                value = float(variable.get())
                if not math.isfinite(value) or not minimum <= value <= maximum:
                    raise ValueError
                if integer and not value.is_integer():
                    raise ValueError
                self.applied_values[key] = str(int(value)) if integer else f"{value:g}"
            except ValueError:
                pass
            variable.set(self.applied_values[key])
        try:
            self.command_id = int(values.get("requestId", "0"))
        except ValueError:
            self.command_id = 0
        try:
            self.coins_request_id = int(values.get("coinsRequestId", "0"))
        except ValueError:
            self.coins_request_id = 0
        try:
            self.level_request_id = int(values.get("levelRequestId", self.command_id))
        except ValueError:
            self.level_request_id = self.command_id
        try:
            self.item_request_id = int(values.get("itemRequestId", self.command_id))
        except ValueError:
            self.item_request_id = self.command_id
        try:
            self.character_request_id = int(values.get("characterRequestId", self.command_id))
        except ValueError:
            self.character_request_id = self.command_id

    def write_config(self, level_request: str | None = None, item_request: str | None = None,
                     character_request: str | None = None, request_id: int | None = None,
                     coins_request_id: int | None = None,
                     level_request_id: int | None = None,
                     item_request_id: int | None = None,
                     character_request_id: int | None = None) -> None:
        if request_id is None:
            request_id = self.command_id
        if coins_request_id is None:
            coins_request_id = self.coins_request_id
        if level_request_id is None:
            level_request_id = self.level_request_id
        if item_request_id is None:
            item_request_id = self.item_request_id
        if character_request_id is None:
            character_request_id = self.character_request_id
        requests = dict(self.pending_requests)
        for key, value in (
            ("levelRequest", level_request), ("itemRequest", item_request),
            ("characterRequest", character_request),
        ):
            if value is not None:
                requests[key] = value
        MOD_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            f"forceBeat={int(self.variables['force_beat'].get())}",
            f"skipMissedBeat={int(self.variables['force_beat'].get())}",
            f"invincible={int(self.variables['invincible'].get())}",
            f"oneHit={int(self.variables['one_hit'].get())}",
            f"doubleDamage={int(self.variables['double_damage'].get())}",
            f"freeShop={int(self.variables['free_shop'].get())}",
            f"infiniteCoins={int(self.variables['free_shop'].get())}",
            f"digAll={int(self.variables['dig_all'].get())}",
            f"unlockAll={int(self.variables['unlock_all'].get())}",
            f"keepCoinsOnTransition={int(self.variables['keep_coins_on_transition'].get())}",
            f"maxHealthValue={self.applied_values['maxHealthValue']}",
            f"coinsValue={self.applied_values['coinsValue']}",
            f"coinsMultiplier={self.applied_values['coinsMultiplier']}",
            f"visionRadius={self.applied_values['visionRadius']}",
            f"coinsRequestId={coins_request_id}",
            f"levelRequestId={level_request_id}",
            f"itemRequestId={item_request_id}",
            f"characterRequestId={character_request_id}",
            f"levelRequest={requests['levelRequest']}",
            f"itemRequest={requests['itemRequest']}",
            f"characterRequest={requests['characterRequest']}",
            f"requestId={request_id}",
        ]
        temporary = MOD_CONFIG.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, MOD_CONFIG)
        self.pending_requests = requests

    def build_ui(self):
        bg = "#1d1f20"
        panel = "#252728"
        text = "#e6e6e6"
        muted = "#a7a9aa"
        accent = "#ff8300"
        track = "#101112"
        self.configure(bg=bg)
        self.overrideredirect(True)
        self.resizable(True, False)
        screen_height = self.winfo_screenheight() / self.scale_factor
        screen_width = self.winfo_screenwidth() / self.scale_factor
        logical_height = min(780, max(520, screen_height - 24))
        logical_width = min(920, max(640, screen_width - 24))
        window_height = self.ui_px(logical_height)
        self.geometry(f"{self.ui_px(logical_width)}x{window_height}")
        self.minsize(
            self.ui_px(min(820, logical_width)),
            self.ui_px(min(650, logical_height)),
        )
        self.maxsize(
            self.ui_px(min(1180, max(logical_width, screen_width - 24))),
            window_height,
        )
        self.bind("<Escape>", lambda _event: self.close())

        self.ui_colors = {
            "bg": bg,
            "panel": panel,
            "text": text,
            "muted": muted,
            "accent": accent,
            "track": track,
            "line": "#414344",
        }
        self.ui_fonts = {
            "header": (self.font_family, 14, "bold"),
            "label": (self.font_family, 11, "bold"),
            "small": (self.font_family, 9),
            "input": (self.font_family, 11),
            "button": (self.font_family, 10, "bold"),
        }

        header = tk.Frame(self, bg=bg, height=self.ui_px(44))
        header.pack(fill="x", padx=self.ui_px(12))
        header.pack_propagate(False)
        self._bind_window_drag(header)
        title_group = tk.Frame(header, bg=bg)
        title_group.pack(side="left", fill="y")
        title_group.bind("<ButtonPress-1>", self._start_window_drag, add="+")
        title_group.bind("<B1-Motion>", self._move_window_drag, add="+")
        tk.Label(title_group, text="NecroDancer Trainer", fg=text, bg=bg,
                 anchor="w", font=self.ui_fonts["header"]).pack(side="left", pady=(7, 0))
        actions = tk.Frame(header, bg=bg)
        actions.pack(side="right", pady=(6, 0))
        for caption, command in (("重新连接", self.attach), ("启动游戏", self.launch_game)):
            button = tk.Button(actions, text=caption, command=command, bg=bg, fg=accent,
                      activebackground=bg, activeforeground="#ffb35c",
                      relief="flat", bd=0, padx=7, cursor="hand2",
                      font=self.ui_fonts["small"], takefocus=0)
            button.pack(side="left")
            self._bind_button_hover(button, accent, "#ffb35c")
        tk.Frame(actions, bg="#555758", width=1, height=20).pack(side="left", padx=5)
        minimize = tk.Button(actions, text="—", command=self.minimize_window, bg=bg, fg=muted,
                             activebackground="#303233", activeforeground=text,
                             relief="flat", bd=0, width=2, cursor="hand2",
                             font=(self.font_family, 11, "bold"), takefocus=0)
        minimize.pack(side="left", padx=1)
        close = tk.Button(actions, text="×", command=self.close, bg=bg, fg="#e26b63",
                          activebackground="#b83d36", activeforeground="#ffffff",
                          relief="flat", bd=0, width=2, cursor="hand2",
                          font=(self.font_family, 12, "bold"), takefocus=0)
        close.pack(side="left", padx=1)
        self._bind_button_hover(minimize, muted, text)
        self._bind_button_hover(close, "#e26b63", "#ffffff")

        tk.Frame(self, bg=self.ui_colors["line"], height=1).pack(fill="x", padx=12)
        main = tk.Frame(self, bg=bg)
        main.pack(fill="both", expand=True, padx=12, pady=(8, 4))
        main.grid_columnconfigure(0, weight=1, uniform="main")
        main.grid_columnconfigure(1, weight=1, uniform="main")
        main.grid_rowconfigure(1, weight=1)
        self._bind_window_drag(main)

        toggle_items = [
            ("force_beat", "强制跟拍", "数字键 1", "跳过丢拍延迟判定"),
            ("invincible", "无敌", "数字键 2", "只保护当前玩家"),
            ("one_hit", "一击必杀", "数字键 3", "玩家攻击立即击杀"),
            ("double_damage", "双倍伤害", "数字键 4", "玩家攻击伤害 x2"),
            ("free_shop", "所有道具免费", "Ctrl+数字键 3", "购买时不扣金币和钻石"),
            ("dig_all", "可挖掘所有方块", "数字键 5", "忽略挖掘强度限制"),
            ("unlock_all", "解锁全部区域和角色", "数字键 6", "包含钥匙区域和隐藏角色"),
            ("keep_coins_on_transition", "区域切换保留金币", "", "进入下一层或区域时保留当前金币"),
        ]
        toggle_panel = tk.Frame(main, bg=panel, padx=10, pady=8)
        toggle_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        tk.Label(toggle_panel, text="功能开关", fg=muted, bg=panel,
                 anchor="w", font=self.ui_fonts["small"]).pack(fill="x", pady=(0, 4))
        value_panel = tk.Frame(main, bg=panel, padx=10, pady=8)
        value_panel.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        tk.Label(value_panel, text="数值设置", fg=muted, bg=panel,
                 anchor="w", font=self.ui_fonts["small"]).pack(fill="x", pady=(0, 4))

        row = 0
        for key, title, hotkey, hint in toggle_items:
            self.add_trainer_row(
                toggle_panel, key, title, hotkey, hint, row, panel, text, muted, accent
            )
            row += 1

        value_items = [
            ("编辑金币", "Ctrl+数字键 1", self.coins_value, self.apply_coins, True, 1),
            ("设置生命上限", "Ctrl+数字键 4", self.max_health_value,
             self.apply_max_health, True, 1),
            ("金币获取倍数", "Ctrl+数字键 8", self.coins_multiplier_value,
             self.apply_coins_multiplier, False, 1),
            ("视野范围（格）", "Ctrl+数字键 9", self.vision_radius_value,
             self.apply_vision_radius, True, 1),
            ("目标关卡", "Ctrl+数字键 7", self.level_value, self.apply_level, True, 1),
            ("编辑永久钻石", "Ctrl+数字键 2", self.diamond_value, self.write_diamonds, True, 1),
        ]
        for title, hotkey, variable, command, integer, step in value_items:
            self.add_value_row(
                value_panel, title, hotkey, variable, command, row,
                panel, text, muted, accent, integer=integer, step=step,
            )
            row += 1
        account_row = tk.Frame(value_panel, bg=panel, height=self.ui_px(32))
        account_row.pack(fill="x", pady=self.ui_px(1))
        account_row.pack_propagate(False)
        tk.Label(account_row, text="存档账号", fg=text, bg=panel,
                 anchor="w", font=self.ui_fonts["label"]).pack(side="left")
        self.save_account_combo = ttk.Combobox(
            account_row, textvariable=self.save_account, state="readonly", width=25,
        )
        self.save_account_combo.pack(side="right", fill="x", expand=True, padx=(8, 0))
        self.save_account_combo.bind("<<ComboboxSelected>>", self.refresh_diamonds)
        self.refresh_save_accounts()

        tools = tk.Frame(main, bg=bg)
        tools.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.add_action_row(
            tools, "显示本层全部道具", "显示道具", self.show_all_items,
            "使用单片眼镜，会替换头部装备", 0, panel, text, muted, accent,
        )
        self.add_action_row(
            tools, "扩大视野范围", "扩大视野", self.increase_vision,
            "装备三级火把，会替换火把", 0, panel, text, muted, accent,
        )
        self.add_action_row(
            tools, "获得道具或武器", "打开道具库", self.open_item_window,
            "按名称、类别或图标选择", 0, panel, text, muted, accent,
        )
        self.add_search_row(
            tools, "角色", "输入中文名或内部 ID",
            self.character_search, self.character_value, self.character_catalog,
            self.apply_character, 0, panel, text, muted, accent,
        )

        footer = tk.Frame(self, bg=bg, height=self.ui_px(30))
        footer.pack(
            fill="x", padx=self.ui_px(12),
            pady=(self.ui_px(2), self.ui_px(3)),
        )
        footer.pack_propagate(False)
        tk.Frame(footer, bg=self.ui_colors["line"], height=1).pack(fill="x")
        status_line = tk.Frame(footer, bg=bg)
        status_line.pack(fill="x", pady=(5, 0))
        tk.Label(status_line, textvariable=self.process_text, fg=text, bg=bg,
                 anchor="w", font=self.ui_fonts["small"]).pack(side="left")
        tk.Label(status_line, textvariable=self.status, fg=muted, bg=bg,
                 anchor="e", font=self.ui_fonts["small"]).pack(side="right")

    def _bind_window_drag(self, widget):
        widget.bind("<ButtonPress-1>", self._start_window_drag, add="+")
        widget.bind("<B1-Motion>", self._move_window_drag, add="+")

    def _start_window_drag(self, event):
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _move_window_drag(self, event):
        offset_x, offset_y = getattr(self, "_drag_offset", (0, 0))
        self.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    @staticmethod
    def _bind_button_hover(button, normal, active):
        button.bind("<Enter>", lambda _event: button.configure(fg=active), add="+")
        button.bind("<Leave>", lambda _event: button.configure(fg=normal), add="+")

    def add_trainer_row(self, parent, key, title, hotkey, hint, row,
                        panel, text, muted, accent):
        card = tk.Frame(parent, bg=panel, height=self.ui_px(31), cursor="hand2")
        card.pack(fill="x")
        card.pack_propagate(False)
        switch = tk.Canvas(card, width=self.ui_px(54), height=self.ui_px(27), bg=panel,
                           highlightthickness=0, bd=0, cursor="hand2")
        switch.pack(side="left", padx=(0, self.ui_px(7)), pady=self.ui_px(2))
        switch.bind("<Button-1>", lambda _event, k=key: self.toggle_feature(k))
        title_label = tk.Label(card, text=title, fg=text, bg=panel,
                               anchor="w", font=self.ui_fonts["label"], cursor="hand2")
        title_label.pack(side="left")
        shortcut = tk.Label(card, text=hotkey, fg=muted, bg=panel,
                            anchor="e", font=self.ui_fonts["small"])
        shortcut.pack(side="right", padx=(4, 2))
        for widget in (card, title_label):
            widget.bind("<Button-1>", lambda _event, k=key: self.toggle_feature(k), add="+")
        self.switches[key] = switch
        self.update_switch(key)

    def add_value_row(self, parent, title, hotkey, variable, command, row,
                       panel, text, muted, accent, integer=False, step=1):
        card = tk.Frame(parent, bg=panel, height=self.ui_px(38))
        card.pack(fill="x", pady=self.ui_px(1))
        card.pack_propagate(False)
        label_group = tk.Frame(card, bg=panel)
        label_group.pack(side="left", fill="y")
        tk.Label(label_group, text=title, fg=text, bg=panel,
                 anchor="w", font=self.ui_fonts["label"]).pack(anchor="w", pady=(1, 0))
        tk.Label(label_group, text=hotkey, fg=muted, bg=panel,
                 anchor="w", font=self.ui_fonts["small"]).pack(anchor="w")
        apply = tk.Button(card, text="应用", command=command, bg=accent, fg="#171717",
                  activebackground="#ffb35c", activeforeground="#171717",
                  relief="flat", bd=0, padx=9, cursor="hand2",
                  font=self.ui_fonts["button"])
        apply.pack(side="right", padx=(6, 0), pady=5)
        entry = tk.Entry(card, textvariable=variable, width=10, justify="center",
                         bg=panel, fg=accent, insertbackground=accent,
                         relief="flat", bd=0, font=self.ui_fonts["input"],
                         highlightthickness=1, highlightbackground="#414344",
                         highlightcolor=accent)
        entry.pack(side="right", padx=(4, 2), pady=5)

    def add_action_row(self, parent, title, button_text, command, hint, row,
                       panel, text, muted, accent):
        button = tk.Button(parent, text=title, command=command, bg=panel, fg=text,
                           activebackground="#303233", activeforeground=text,
                           relief="flat", bd=0, padx=10, pady=7, cursor="hand2",
                           font=self.ui_fonts["small"])
        button.pack(side="left", padx=(0, 5))
        button.bind("<Enter>", lambda _event: button.configure(fg=accent), add="+")
        button.bind("<Leave>", lambda _event: button.configure(fg=text), add="+")

    def add_search_row(self, parent, title, hint, search_var, value_var, catalog,
                       command, row, panel, text, muted, accent):
        card = tk.Frame(parent, bg=panel)
        card.pack(side="right", fill="x", expand=True, padx=(8, 0))
        apply = tk.Button(card, text="切换", command=command, bg=accent, fg="#171717",
                  activebackground="#ffb35c", activeforeground="#171717",
                  relief="flat", bd=0, padx=9, cursor="hand2",
                  font=self.ui_fonts["button"])
        apply.pack(side="right", padx=(6, 0), pady=3)
        search = tk.Entry(card, textvariable=search_var, bg=panel, fg=text,
                          insertbackground=text, relief="flat", bd=0, width=19,
                          font=self.ui_fonts["input"],
                          highlightthickness=1, highlightbackground="#414344",
                          highlightcolor=accent)
        search.pack(side="right", fill="x", expand=True, pady=3)
        tk.Label(card, text=title, fg=muted, bg=panel,
                 font=self.ui_fonts["small"]).pack(side="right", padx=(0, 7))
        search_id = id(search)
        self._create_search_popup(search, search_var, value_var, catalog, search_id, text, muted, accent)

        def refresh(_event=None):
            query = search_var.get().strip()
            matches = self.catalog_matches(query, catalog)
            self.search_matches[search_id] = matches[:30]
            popup = self.search_popups[search_id]
            listing = self.search_lists[search_id]
            listing.delete(0, "end")
            for internal, display in matches[:30]:
                listing.insert("end", f"{display}  [{internal}]")
            if matches:
                self._show_search_popup(search, search_id)
            else:
                popup.withdraw()

        def choose(_event=None):
            listing = self.search_lists[search_id]
            selection = listing.curselection()
            matches = self.search_matches.get(search_id, [])
            if not selection or not matches:
                return
            internal, display = matches[selection[0]]
            value_var.set(internal)
            search_var.set(display)
            self.search_popups[search_id].withdraw()
            self.status.set(f"已选择{display}，可点击“切换”应用")

        search.bind("<KeyRelease>", refresh)
        search.bind("<Return>", choose)
        search.bind("<Down>", lambda _event: self._focus_search_list(search_id))
        def hide_search_popup(_event=None):
            self.search_popups[search_id].withdraw()
            return "break"

        search.bind("<Escape>", hide_search_popup)
        self.search_lists[search_id].bind("<Return>", choose)
        self.search_lists[search_id].bind("<Double-Button-1>", choose)
        self.search_lists[search_id].bind("<Escape>", hide_search_popup)

    def _create_search_popup(self, search, search_var, value_var, catalog,
                             search_id, text, muted, accent):
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self)
        popup.configure(bg="#4c4e50")
        popup.bind("<FocusOut>", lambda _event: self.after(
            120, lambda: self._hide_search_popup_if_unfocused(search_id)
        ))
        listing = tk.Listbox(
            popup, height=6, width=42, bg=self.ui_colors["track"], fg=text,
            selectbackground="#8a4a00", selectforeground="#ffffff",
            activestyle="none", relief="flat", bd=0, highlightthickness=0,
            exportselection=False, font=self.ui_fonts["small"],
        )
        listing.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self.search_popups[search_id] = popup
        self.search_lists[search_id] = listing
        self.search_matches[search_id] = []

    def _show_search_popup(self, search, search_id):
        popup = self.search_popups[search_id]
        listing = self.search_lists[search_id]
        popup.update_idletasks()
        x = search.winfo_rootx()
        y = search.winfo_rooty() + search.winfo_height() + 2
        height = min(
            self.ui_px(180),
            self.ui_px(2 + max(1, len(self.search_matches[search_id])) * 24),
        )
        popup.geometry(f"{self.ui_px(420)}x{height}+{x}+{y}")
        popup.deiconify()
        popup.lift()
        listing.selection_clear(0, "end")
        listing.selection_set(0)

    def _focus_search_list(self, search_id):
        listing = self.search_lists[search_id]
        if listing.size():
            listing.focus_set()
            listing.selection_clear(0, "end")
            listing.selection_set(0)
        return "break"

    def _hide_search_popup_if_unfocused(self, search_id):
        popup = self.search_popups.get(search_id)
        if popup is None:
            return
        focused = self.focus_get()
        if focused not in (popup, self.search_lists.get(search_id)):
            popup.withdraw()

    def adjust_value(self, variable, delta, integer=False):
        try:
            current = float(variable.get())
            if not math.isfinite(current):
                raise ValueError
        except ValueError:
            current = 0
        value = current + delta
        variable.set(str(int(value)) if integer else f"{value:.1f}".rstrip("0").rstrip("."))

    def attach(self):
        try:
            self.ensure_mod_enabled()
            found = self.finder.find()
        except (OSError, PermissionError) as exc:
            self.status.set(f"连接检查失败：{exc}")
            return False
        if found:
            pid, name, executable = found
            self.process_text.set(f"已连接  ·  {name}  PID {pid}")
            if executable:
                game_dir = executable.parent.parent if executable.parent.name.casefold() == "necrodancer64" else executable.parent
                self.set_game_dir(game_dir)
                self.path_text.set(self.game_dir_text())
                self.status.set("已连接游戏；模组配置已同步")
            else:
                self.status.set("已检测到游戏进程，但无法读取程序路径；存档保护仍已启用")
            return True
        self.process_text.set("游戏未运行")
        self.status.set("未检测到游戏")
        return False

    def poll(self):
        try:
            self.ensure_mod_enabled()
            found = self.finder.find()
        except (OSError, PermissionError) as exc:
            self.status.set(f"同步失败：{exc}")
            self.after(800, self.poll)
            return
        if found:
            pid, name, executable = found
            self.process_text.set(f"已连接  ·  {name}  PID {pid}")
            if executable:
                game_dir = executable.parent.parent if executable.parent.name.casefold() == "necrodancer64" else executable.parent
                self.set_game_dir(game_dir)
                self.path_text.set(self.game_dir_text())
            else:
                self.status.set("已检测到游戏进程，但无法读取程序路径；存档保护仍已启用")
        else:
            self.process_text.set("游戏未运行")
        self.after(800, self.poll)

    def set_game_dir(self, game_dir: Path) -> None:
        if game_dir == self.game_dir:
            return
        self.game_dir = game_dir
        self.item_metadata.clear()
        self.item_icon_cache.clear()
        self.item_catalog = self.load_item_catalog()
        self.character_catalog = self.load_character_catalog()

    def find_steam(self) -> Path | None:
        candidates = [
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Steam" / "steam.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Steam" / "steam.exe",
        ]
        try:
            import winreg

            for root, key_name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(root, key_name) as key:
                        install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                        candidates.append(Path(install_path) / "steam.exe")
                except OSError:
                    pass
        except ImportError:
            pass
        return next((path for path in candidates if path.is_file()), None)

    def launch_game(self):
        self.ensure_mod_enabled()
        if self.finder.find():
            self.attach()
            return
        steam = self.find_steam()
        try:
            if steam:
                subprocess.Popen([str(steam), "-applaunch", STEAM_APP_ID],
                                 cwd=str(steam.parent), creationflags=CREATE_NO_WINDOW)
            elif self.game_dir and (self.game_dir / "Necrodancer64" / "Necrodancer.exe").is_file():
                subprocess.Popen(
                    [str(self.game_dir / "Necrodancer64" / "Necrodancer.exe")],
                    cwd=str(self.game_dir / "Necrodancer64"),
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
            self.status.set("正在通过 Steam 启动游戏…")
        except (OSError, AttributeError) as exc:
            messagebox.showerror("启动失败", str(exc))

    def feature_changed(self, key):
        self.write_config()
        state = "已开启" if self.variables[key].get() else "已关闭"
        labels = {
            "force_beat": "强制跟拍",
            "invincible": "无敌",
            "one_hit": "一击必杀",
            "double_damage": "双倍伤害",
            "free_shop": "商店免费购买",
            "dig_all": "可挖掘所有方块",
            "unlock_all": "解锁全部区域和角色",
            "keep_coins_on_transition": "区域切换保留金币",
        }
        self.status.set(f"{labels[key]}{state}；游戏内配置将在下一次刷新时读取")

    def toggle_feature(self, key):
        self.variables[key].set(not self.variables[key].get())
        self.update_switch(key)
        self.feature_changed(key)
        if key == "unlock_all" and self.variables[key].get():
            self.unlock_all_regions()

    def update_switch(self, key):
        switch = self.switches.get(key)
        if switch is None:
            return
        active = self.variables[key].get()
        switch.delete("all")
        track_color = self.ui_colors["accent"] if active else self.ui_colors["track"]
        knob_color = "#ffffff"
        scale = self.ui_px
        switch.create_oval(scale(1), scale(1), scale(27), scale(27), fill=track_color, outline=track_color)
        switch.create_rectangle(scale(14), scale(1), scale(40), scale(27), fill=track_color, outline=track_color)
        switch.create_oval(scale(27), scale(1), scale(53), scale(27), fill=track_color, outline=track_color)
        knob_x = scale(27 if active else 1)
        switch.create_oval(knob_x, scale(2), knob_x + scale(23), scale(25), fill=knob_color, outline=knob_color)

    def apply_max_health(self):
        try:
            value = int(self.max_health_value.get())
            if value < 1 or value > 999:
                raise ValueError
        except ValueError:
            messagebox.showwarning("数值无效", "生命上限请输入 1 到 999 之间的整数")
            return
        self.max_health_value.set(str(value))
        self.applied_values["maxHealthValue"] = str(value)
        self.write_config()
        self.status.set(f"已设置生命上限为 {value}；游戏内将在下一次刷新时应用")

    def apply_coins(self):
        try:
            value = int(self.coins_value.get())
            if value < 0 or value > 2_147_483_647:
                raise ValueError
        except ValueError:
            messagebox.showwarning("数值无效", "金币请输入 0 到 2147483647 之间的整数")
            return
        self.coins_value.set(str(value))
        self.applied_values["coinsValue"] = str(value)
        self.coins_request_id += 1
        self.write_config(coins_request_id=self.coins_request_id)
        self.status.set(f"已请求将当前金币设为 {value}")

    def apply_coins_multiplier(self):
        try:
            value = float(self.coins_multiplier_value.get())
            if not math.isfinite(value) or value < 1 or value > 100:
                raise ValueError
        except ValueError:
            messagebox.showwarning("数值无效", "金币获取倍数请输入 1 到 100")
            return
        self.coins_multiplier_value.set(f"{value:g}")
        self.applied_values["coinsMultiplier"] = f"{value:g}"
        self.write_config()
        self.status.set(f"金币获取倍数已设为 {value:g} 倍")

    def apply_vision_radius(self):
        try:
            value = int(self.vision_radius_value.get())
            if value < 0 or value > 100:
                raise ValueError
        except ValueError:
            messagebox.showwarning("数值无效", "视野范围请输入 0 到 100 格之间的整数")
            return
        self.vision_radius_value.set(str(value))
        self.applied_values["visionRadius"] = str(value)
        self.write_config()
        self.status.set(
            "已关闭自定义视野设置"
            if value == 0
            else f"视野半径已设为 {value} 格；进入关卡后应用"
        )

    def apply_level(self):
        try:
            value = int(self.level_value.get())
            if value < 1 or value > 99:
                raise ValueError
        except ValueError:
            messagebox.showwarning("关卡无效", "目标关卡请输入 1 到 99 之间的整数")
            return
        self.level_value.set(str(value))
        self.command_id += 1
        self.level_request_id += 1
        self.write_config(
            level_request=str(value),
            request_id=self.command_id,
            level_request_id=self.level_request_id,
        )
        self.status.set(f"已请求跳转到第 {value} 层；请在游戏内等待下一次刷新")

    def request_item(self, item_id: str):
        value = self.resolve_catalog_value(item_id, self.item_catalog, self.item_aliases)
        if value not in {item for item, _name in self.item_catalog}:
            messagebox.showwarning("道具 ID 无效", "请输入游戏内部道具 ID，例如 weapon_longsword")
            return
        self.item_value.set(value)
        self.command_id += 1
        self.item_request_id += 1
        self.write_config(
            item_request=value,
            request_id=self.command_id,
            item_request_id=self.item_request_id,
        )
        display = next(
            (name for item, name in self.item_catalog if item == value),
            value,
        )
        display = self.item_name_in_chinese(value, display)
        self.status.set(f"已请求获得 {display}；游戏内将在下一次刷新时执行")
        if self.item_window_status is not None:
            self.item_window_status.set(f"已请求：{display}")

    def apply_item(self):
        requested = self.item_value.get().strip()
        value = self.resolve_catalog_value(
            requested, self.item_catalog, self.item_aliases
        )
        if not value or any(char in value for char in "\r\n="):
            messagebox.showwarning("道具 ID 无效", "请输入游戏内部道具 ID，例如 weapon_longsword")
            return
        self.request_item(value)

    def show_all_items(self):
        self.command_id += 1
        self.item_request_id += 1
        self.write_config(
            item_request="head_monocle",
            request_id=self.command_id,
            item_request_id=self.item_request_id,
        )
        self.status.set("已请求使用单片眼镜显示本层全部道具；头部装备槽会被替换")

    def increase_vision(self):
        self.command_id += 1
        self.item_request_id += 1
        self.write_config(
            item_request="torch_3",
            request_id=self.command_id,
            item_request_id=self.item_request_id,
        )
        self.status.set("已请求装备三级火把，增加 3 格视野；当前火把会被替换")

    def apply_character(self):
        requested = self.character_search.get().strip() or self.character_value.get().strip()
        # Keep character matching to IDs, display names, and explicit aliases.
        value = self.resolve_catalog_value(
            requested, self.character_catalog, self.character_aliases,
            allow_generated=False,
        )
        if value not in {internal for internal, _display in self.character_catalog}:
            messagebox.showwarning("角色 ID 无效", "请输入角色中文名或内部 ID，例如 卡登斯 / Cadence")
            return
        self.character_value.set(value)
        self.command_id += 1
        self.character_request_id += 1
        self.write_config(
            character_request=value,
            request_id=self.command_id,
            character_request_id=self.character_request_id,
        )
        self.status.set(f"已请求切换角色为 {value}；游戏内将在下一次刷新时执行")

    @staticmethod
    def resolve_catalog_value(
        value: str,
        catalog: list[tuple[str, str]],
        aliases: dict[str, tuple[str, ...]] | None = None,
        allow_generated: bool = True,
    ) -> str:
        folded = value.casefold()
        matches = set()
        for internal, display in catalog:
            names = [internal, display]
            if aliases:
                names.extend(aliases.get(internal, ()))
            if allow_generated:
                names.append(Trainer.item_name_in_chinese(internal, display))
            if any(folded == name.casefold() for name in names):
                matches.add(internal)
        # A name shared by several catalog entries must stay unresolved so the
        # caller never silently requests the first item that happens to match.
        if len(matches) == 1:
            return matches.pop()
        return value

    def save_files(self) -> list[Path]:
        return sorted(SAVE_DIR.glob("SynchronyUser_*.lua"), key=lambda path: path.name.casefold())

    def refresh_save_accounts(self, _event=None):
        files = self.save_files()
        names = [path.name for path in files]
        current = self.save_account.get()
        if current not in names:
            had_selection = bool(current)
            current = (
                names[0]
                if not current and not self.save_account_invalidated and len(names) == 1
                else ""
            )
            self.save_account.set(current)
            self.diamond_value.set("")
            self.diamond_account = ""
            if had_selection:
                self.save_account_invalidated = True
            if had_selection:
                self.status.set("存档账号已变更，请重新选择账号")
        else:
            self.save_account_invalidated = False
        if self.save_account_combo is not None:
            self.save_account_combo.configure(values=names)
        return files

    def latest_save(self) -> Path | None:
        files = self.refresh_save_accounts()
        selected = self.save_account.get()
        if selected:
            return next((path for path in files if path.name == selected), None)
        if len(files) == 1 and not self.save_account_invalidated:
            return files[0]
        return None

    def refresh_diamonds(self, _event=None):
        self.diamond_value.set("")
        self.diamond_account = ""
        self.refresh_save_accounts()
        path = self.latest_save()
        if not path:
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self.status.set(f"无法读取存档钻石：{exc}")
            return
        match = re.search(r'\["progression\.diamondCounter"\]\s*=\s*(\d+)', text)
        if match:
            self.diamond_value.set(match.group(1))
            self.diamond_account = path.name
        else:
            self.status.set("存档中没有找到钻石字段")

    def write_diamonds(self):
        if self.game_is_running():
            self.status.set("请先退出游戏，再修改永久钻石，避免存档被游戏覆盖")
            return
        path = self.latest_save()
        if not path:
            self.status.set("未找到存档，请先确认存档目录或选择存档账号")
            return
        if self.diamond_account != path.name:
            self.diamond_value.set("")
            self.status.set("钻石数未从当前账号加载，请重新选择存档账号")
            return
        try:
            value = int(self.diamond_value.get())
            if value < 0 or value > 2_147_483_647:
                raise ValueError
        except ValueError:
            messagebox.showwarning("数量无效", "请输入 0 到 2147483647 之间的整数")
            return
        try:
            original = path.read_bytes()
            text = original.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            self.status.set(f"无法读取存档：{exc}")
            return
        updated, count = re.subn(
            r'(\["progression\.diamondCounter"\]\s*=\s*)\d+',
            rf"\g<1>{value}",
            text,
            count=1,
        )
        if count != 1:
            self.status.set("存档中没有找到钻石字段")
            return
        try:
            backup = backup_and_replace_save(
                path, original, updated, "before_diamonds",
                before_replace=lambda: self.require_game_stopped(
                    "游戏已启动，已取消钻石写入"
                ),
            )
        except GameRunningError as exc:
            self.status.set(str(exc))
            return
        except OSError as exc:
            self.status.set(f"写入钻石失败，原存档未替换：{exc}")
            return
        self.status.set(f"已写入 {value} 颗钻石；备份：{backup.name}")

    def unlock_all_regions(self):
        if self.game_is_running():
            self.status.set("已开启游戏内解锁；游戏运行时不直接改写存档")
            return
        path = self.latest_save()
        if not path:
            self.status.set("未选择存档账号或未找到存档；角色解锁仍由游戏内 Mod 处理")
            return
        try:
            original = path.read_bytes()
            text = original.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            self.status.set(f"无法读取存档：{exc}")
            return

        updated = text
        changed = 0
        for zone in range(1, 6):
            pattern = rf"(NumZone{zone}Completions\s*=\s*)\d+"
            updated, count = re.subn(pattern, rf"\g<1>9999", updated, count=1)
            changed += count
        if changed == 0:
            self.status.set("当前存档格式未找到区域完成字段；角色解锁由游戏内 Mod 处理")
            return

        try:
            backup = backup_and_replace_save(
                path, original, updated, "before_unlock",
                before_replace=lambda: self.require_game_stopped(
                    "游戏已启动，已取消区域解锁写入"
                ),
            )
        except GameRunningError as exc:
            self.status.set(str(exc))
            return
        except OSError as exc:
            self.status.set(f"写入区域解锁失败，原存档未替换：{exc}")
            return
        self.status.set(
            f"已写入区域 1 到 5 的完成状态；备份：{backup.name}。"
            "请重启游戏读取。角色解锁由游戏内 Mod 处理"
        )


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("仅支持 Windows")
    try:
        Trainer().mainloop()
    except Exception as exc:
        messagebox.showerror(
            "NecroDancer Trainer 启动失败",
            f"初始化或运行时发生错误：\n{exc}\n\n请检查游戏目录、模组目录和配置文件权限。",
        )
