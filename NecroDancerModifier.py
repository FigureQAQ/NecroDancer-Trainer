from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


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
MOD_VERSION = "1.0.0"
PROCESS_NAMES = {"necrodancer.exe", "necrodancer64.exe"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
TH32CS_SNAPPROCESS = 0x00000002
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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

    def find(self) -> tuple[int, str, Path] | None:
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
                    if path:
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


class Switch(tk.Canvas):
    def __init__(self, parent: tk.Misc, variable: tk.BooleanVar, command, enabled: bool = True):
        super().__init__(
            parent,
            width=54,
            height=28,
            bg="#192338",
            highlightthickness=0,
            cursor="hand2" if enabled else "arrow",
        )
        self.variable = variable
        self.command = command
        self.enabled = enabled
        self.bind("<Button-1>", self._click)
        self.draw()

    def _click(self, _event=None):
        if self.enabled:
            self.variable.set(not self.variable.get())
            self.draw()
            self.command()
        return "break"

    def draw(self):
        self.delete("all")
        active = self.variable.get() and self.enabled
        color = "#e23b43" if active else ("#3e4b61" if self.enabled else "#293345")
        self.create_rounded(2, 2, 52, 26, 12, fill=color, outline="")
        x = 38 if active else 16
        self.create_oval(x - 9, 5, x + 9, 23, fill="#ffffff" if self.enabled else "#687386", outline="")

    def create_rounded(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1 + radius,
            x2, y2 - radius, x2 - radius, y2, x1 + radius, y2,
            x1, y2 - radius, x1, y1 + radius,
        ]
        self.create_polygon(points, smooth=True, **kwargs)


class Trainer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NecroDancer Trainer")
        self.geometry("920x640")
        self.resizable(False, False)
        self.configure(bg="#0e1624")
        self.finder = ProcessFinder()
        self.game_dir = self.find_game_dir()
        self.status = tk.StringVar(value="未连接")
        self.process_text = tk.StringVar(value="游戏未运行")
        self.path_text = tk.StringVar(value=self.game_dir_text())
        self.diamond_value = tk.StringVar(value="")
        self.variables = {
            "invincible": tk.BooleanVar(value=False),
            "one_hit": tk.BooleanVar(value=False),
            "double_damage": tk.BooleanVar(value=False),
            "coins": tk.BooleanVar(value=False),
            "diamonds": tk.BooleanVar(value=False),
        }
        self.switches: dict[str, Switch] = {}
        self.ensure_mod_files()
        self.load_config()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(800, self.poll)
        self.refresh_diamonds()

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
            self.write_config()
        self.ensure_mod_enabled()

    def ensure_mod_enabled(self) -> None:
        save_root = APPDATA / "NecroDancer"
        option_files = [save_root / "SynchronyOptions.lua"]
        option_files.extend(save_root.glob("SynchronyUser_*.lua"))
        marker = '["modLoader.list"] = {'
        for options in option_files:
            if not options.is_file():
                continue
            try:
                text = options.read_text(encoding="utf-8")
            except OSError:
                continue
            if 'name="NecroDancerTrainer"' in text or marker not in text:
                continue
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup = BACKUP_DIR / f"{options.name}.before_trainer_{time.strftime('%Y%m%d-%H%M%S')}.bak"
            if not backup.exists():
                backup.write_text(text, encoding="utf-8", newline="")
            updated = text.replace(
                marker,
                f'{marker}{{name="NecroDancerTrainer",package=false,version="{MOD_VERSION}"}},',
                1,
            )
            options.write_text(updated, encoding="utf-8", newline="")

    def load_config(self) -> None:
        values = {}
        try:
            for line in MOD_CONFIG.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    values[key.strip()] = value.strip() == "1"
        except OSError:
            values = {}
        for key in ("invincible", "oneHit", "doubleDamage", "infiniteCoins"):
            ui_key = {
                "oneHit": "one_hit",
                "doubleDamage": "double_damage",
                "infiniteCoins": "coins",
            }.get(key, key)
            self.variables[ui_key].set(values.get(key, False))

    def write_config(self) -> None:
        MOD_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            f"invincible={int(self.variables['invincible'].get())}",
            f"oneHit={int(self.variables['one_hit'].get())}",
            f"doubleDamage={int(self.variables['double_damage'].get())}",
            f"infiniteCoins={int(self.variables['coins'].get())}",
        ]
        temporary = MOD_CONFIG.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, MOD_CONFIG)

    def build_ui(self):
        header = tk.Frame(self, bg="#0e1624")
        header.pack(fill="x", padx=28, pady=(24, 5))
        tk.Label(header, text="NECRODANCER", fg="#f4f6fb", bg="#0e1624",
                 font=("Segoe UI", 24, "bold")).pack(side="left")
        tk.Label(header, text=" TRAINER", fg="#e23b43", bg="#0e1624",
                 font=("Segoe UI", 24, "bold")).pack(side="left")
        tk.Label(self, text="本地单机修改器 · 低负载模式", fg="#8290a5", bg="#0e1624",
                 font=("Segoe UI", 10)).pack(anchor="w", padx=31)

        bar = tk.Frame(self, bg="#182337")
        bar.pack(fill="x", padx=28, pady=(18, 12))
        connection = tk.Frame(bar, bg="#182337")
        connection.pack(side="left", fill="x", expand=True, padx=16, pady=9)
        tk.Label(connection, textvariable=self.process_text, fg="#dce4ef", bg="#182337",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(connection, textvariable=self.path_text, fg="#718096", bg="#182337",
                 font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))
        tk.Button(bar, text="启动游戏", command=self.launch_game, bg="#34445d", fg="white",
                  activebackground="#465b7a", relief="flat", padx=14, pady=5).pack(side="right", padx=8)
        tk.Button(bar, text="重新连接", command=self.attach, bg="#27364c", fg="white",
                  activebackground="#465b7a", relief="flat", padx=14, pady=5).pack(side="right", padx=0)

        body = tk.Frame(self, bg="#0e1624")
        body.pack(fill="both", expand=True, padx=28)
        side = tk.Frame(body, bg="#182337", width=190)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="功能分类", fg="#8795a9", bg="#182337",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=18, pady=(20, 12))
        tk.Label(side, text="⚔  战斗功能", fg="white", bg="#2a3b55",
                 anchor="w", padx=18, pady=11, font=("Segoe UI", 10, "bold")).pack(fill="x")
        tk.Label(side, text="◆  资源功能", fg="#8795a9", bg="#182337",
                 anchor="w", padx=18, pady=11).pack(fill="x")
        tk.Label(side, text="⚙  使用说明", fg="#8795a9", bg="#182337",
                 anchor="w", padx=18, pady=11).pack(fill="x")
        tk.Label(side, text="战斗开关通过本地模组\n读取配置，不扫描内存。\n修改器会自动备份存档，\n关闭游戏后再改钻石。", fg="#718096", bg="#182337",
                 justify="left", anchor="nw", padx=18, pady=28,
                 font=("Segoe UI", 9)).pack(fill="both", expand=True)

        content = tk.Frame(body, bg="#0e1624")
        content.pack(side="left", fill="both", expand=True, padx=(22, 0))
        tk.Label(content, text="修改选项", fg="#f4f6fb", bg="#0e1624",
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(4, 3))
        tk.Label(content, text="开关状态会写入本地模组配置，游戏内每个节拍低频刷新", fg="#8290a5", bg="#0e1624",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 14))

        self.add_row(content, "invincible", "无敌", "玩家不受到伤害", True)
        self.add_row(content, "one_hit", "一击必杀", "玩家攻击时将敌人伤害拉满", True)
        self.add_row(content, "double_damage", "双倍伤害", "玩家攻击伤害乘以 2", True)
        self.add_row(content, "coins", "无限金币", "将玩家当前金币维持在高数值", True)
        self.add_row(content, "diamonds", "钻石修改", "修改存档中的永久钻石数量", True)

        diamond = tk.Frame(content, bg="#182337")
        diamond.pack(fill="x", pady=(10, 0))
        tk.Label(diamond, text="永久钻石数量", fg="#cbd5e1", bg="#182337").pack(side="left", padx=14, pady=12)
        tk.Entry(diamond, textvariable=self.diamond_value, width=10, bg="#0f1929", fg="white",
                 insertbackground="white", relief="flat").pack(side="left", padx=5)
        tk.Button(diamond, text="写入存档", command=self.write_diamonds, bg="#e23b43", fg="white",
                  activebackground="#b72e35", relief="flat", padx=12, pady=4).pack(side="left", padx=8)
        tk.Label(content, textvariable=self.status, fg="#9aa8bb", bg="#0e1624",
                 anchor="w").pack(fill="x", pady=(20, 0))

    def add_row(self, parent, key, title, description, enabled):
        row = tk.Frame(parent, bg="#182337", height=64)
        row.pack(fill="x", pady=4)
        row.pack_propagate(False)
        text = tk.Frame(row, bg="#182337")
        text.pack(side="left", fill="both", expand=True, padx=14)
        title_label = tk.Label(text, text=title, fg="#e8edf5" if enabled else "#8995a7",
                               bg="#182337", font=("Segoe UI", 11, "bold"), anchor="w")
        title_label.pack(anchor="w", pady=(11, 0))
        description_label = tk.Label(text, text=description, fg="#76859a", bg="#182337",
                                     font=("Segoe UI", 9), anchor="w")
        description_label.pack(anchor="w")
        switch = Switch(row, self.variables[key], lambda k=key: self.feature_changed(k), enabled)
        switch.pack(side="right", padx=14, pady=18)
        self.switches[key] = switch

        def toggle_from_row(_event=None):
            switch._click()
            return "break"

        for widget in (row, text, title_label, description_label):
            widget.bind("<Button-1>", toggle_from_row)

    def attach(self):
        found = self.finder.find()
        if found:
            pid, name, executable = found
            self.game_dir = executable.parent.parent if executable.parent.name.casefold() == "necrodancer64" else executable.parent
            self.path_text.set(self.game_dir_text())
            self.process_text.set(f"已连接  ·  {name}  PID {pid}")
            self.status.set("已连接游戏；模组配置已同步")
            return True
        self.process_text.set("游戏未运行")
        self.status.set("未检测到游戏")
        return False

    def poll(self):
        found = self.finder.find()
        if found:
            pid, name, executable = found
            self.game_dir = executable.parent.parent if executable.parent.name.casefold() == "necrodancer64" else executable.parent
            self.path_text.set(self.game_dir_text())
            self.process_text.set(f"已连接  ·  {name}  PID {pid}")
        else:
            self.process_text.set("游戏未运行")
        self.after(800, self.poll)

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
        if key == "diamonds":
            self.variables[key].set(False)
            self.switches[key].draw()
            self.status.set("请在下方输入钻石数量后点击“写入存档”")
            return
        self.write_config()
        self.switches[key].draw()
        state = "已开启" if self.variables[key].get() else "已关闭"
        labels = {
            "invincible": "无敌",
            "one_hit": "一击必杀",
            "double_damage": "双倍伤害",
            "coins": "无限金币",
        }
        self.status.set(f"{labels[key]}{state}；游戏内配置将在下一次刷新时读取")

    def latest_save(self) -> Path | None:
        files = sorted(SAVE_DIR.glob("SynchronyUser_*.lua"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        return files[0] if files else None

    def refresh_diamonds(self):
        path = self.latest_save()
        if not path:
            return
        match = re.search(r'\["progression\.diamondCounter"\]\s*=\s*(\d+)', path.read_text(encoding="utf-8"))
        if match:
            self.diamond_value.set(match.group(1))

    def write_diamonds(self):
        path = self.latest_save()
        if not path:
            self.status.set("未找到存档")
            return
        try:
            value = int(self.diamond_value.get())
            if value < 0 or value > 2_147_483_647:
                raise ValueError
        except ValueError:
            messagebox.showwarning("数量无效", "请输入 0 到 2147483647 之间的整数")
            return
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        backup.write_bytes(path.read_bytes())
        text = path.read_text(encoding="utf-8")
        updated, count = re.subn(
            r'(\["progression\.diamondCounter"\]\s*=\s*)\d+',
            rf"\g<1>{value}",
            text,
            count=1,
        )
        if count != 1:
            self.status.set("存档中没有找到钻石字段")
            return
        path.write_text(updated, encoding="utf-8", newline="")
        self.variables["diamonds"].set(True)
        self.switches["diamonds"].draw()
        self.status.set(f"已写入 {value} 颗钻石；备份已保存")


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("仅支持 Windows")
    Trainer().mainloop()
