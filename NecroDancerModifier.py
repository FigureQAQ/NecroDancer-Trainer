from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk


GAME_DIR = Path(__file__).resolve().parent
GAME_EXE = GAME_DIR / "Necrodancer64" / "Necrodancer.exe"
STEAM_APP_ID = "247080"
APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
SAVE_DIR = APPDATA / "NecroDancer"
BACKUP_DIR = SAVE_DIR / "ModifierBackups"
PROCESS_NAMES = {"necrodancer.exe", "necrodancer64.exe"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
TH32CS_SNAPPROCESS = 0x00000002
STILL_ACTIVE = 259


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

    def find(self) -> tuple[int, str] | None:
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
                    return int(entry.th32ProcessID), entry.szExeFile
                if not self.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    return None
        finally:
            self.kernel32.CloseHandle(snapshot)

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
        self.geometry("880x600")
        self.resizable(False, False)
        self.configure(bg="#0e1624")
        self.finder = ProcessFinder()
        self.status = tk.StringVar(value="未连接")
        self.process_text = tk.StringVar(value="游戏未运行")
        self.diamond_value = tk.StringVar(value="")
        self.variables = {
            "invincible": tk.BooleanVar(value=False),
            "one_hit": tk.BooleanVar(value=False),
            "double_damage": tk.BooleanVar(value=False),
            "coins": tk.BooleanVar(value=False),
            "diamonds": tk.BooleanVar(value=False),
        }
        self.switches: dict[str, Switch] = {}
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(1000, self.poll)
        self.refresh_diamonds()

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
        tk.Label(bar, textvariable=self.process_text, fg="#dce4ef", bg="#182337",
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=16, pady=13)
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
        tk.Label(side, text="战斗项需要游戏内\n受支持的模组接口。\n当前版本未启用未经验证\n的内存修改。", fg="#718096", bg="#182337",
                 justify="left", anchor="nw", padx=18, pady=28,
                 font=("Segoe UI", 9)).pack(fill="both", expand=True)

        content = tk.Frame(body, bg="#0e1624")
        content.pack(side="left", fill="both", expand=True, padx=(22, 0))
        tk.Label(content, text="修改选项", fg="#f4f6fb", bg="#0e1624",
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(4, 3))
        tk.Label(content, text="只有已验证功能会允许开启", fg="#8290a5", bg="#0e1624",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 14))

        self.add_row(content, "invincible", "无敌", "战斗中免疫伤害", False)
        self.add_row(content, "one_hit", "一击必杀", "攻击时直接击败敌人", False)
        self.add_row(content, "double_damage", "双倍伤害", "造成 2 倍攻击伤害", False)
        self.add_row(content, "coins", "无限金币", "当前版本未验证，暂不启用", False)
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
        enabled = True
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
            self.process_text.set(f"已连接  ·  {found[1]}  PID {found[0]}")
            self.status.set("已连接游戏；当前仅启用存档钻石修改")
            return True
        self.process_text.set("游戏未运行")
        self.status.set("未检测到游戏")
        return False

    def poll(self):
        found = self.finder.find()
        if found:
            self.process_text.set(f"已连接  ·  {found[1]}  PID {found[0]}")
        else:
            self.process_text.set("游戏未运行")
        self.after(1200, self.poll)

    def launch_game(self):
        if self.finder.find():
            self.attach()
            return
        steam = Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Steam" / "steam.exe"
        if not steam.exists():
            steam = Path(os.environ.get("PROGRAMFILES", "")) / "Steam" / "steam.exe"
        try:
            if steam.exists():
                subprocess.Popen([str(steam), "-applaunch", STEAM_APP_ID],
                                 cwd=str(steam.parent), creationflags=CREATE_NO_WINDOW)
            else:
                os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
            self.status.set("正在通过 Steam 启动游戏…")
        except (OSError, AttributeError) as exc:
            messagebox.showerror("启动失败", str(exc))

    def feature_changed(self, key):
        if key == "invincible":
            self.switches[key].draw()
            self.status.set("无敌已启用；该功能随游戏启动加载")
            return
        self.switches[key].draw()
        self.status.set("该功能尚未针对当前游戏版本完成验证，已保持关闭")

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
