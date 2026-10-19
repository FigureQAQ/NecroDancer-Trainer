# Crypt of the NecroDancer Trainer

一个使用 Python/Tkinter 制作的《Crypt of the NecroDancer》图形化修改器。

## 当前状态

- 支持通过真实进程路径检测游戏，不扫描内存。
- 支持从 Steam 启动游戏，找不到 Steam 时尝试直接启动游戏。
- 支持自动部署本地 unpacked mod，并写入当前用户的 mod 列表。
- 无敌、一击必杀、双倍伤害和无限金币开关通过本地模组配置驱动。
- 支持修改存档中的永久钻石数量，并在修改前自动备份存档。
- 已验证当前 `v4.2.1-b5713` 模组可以正常挂载且启动无 Lua 错误；战斗效果仍应在单机房间内自行确认。

## 使用方法

1. 下载 `dist/NecroDancerTrainer_Invincible.exe`。
2. 双击 EXE，修改器会自动定位 Steam 游戏目录并部署模组。
3. 点击开关后启动或重新启动游戏；配置文件位于 `%LOCALAPPDATA%\NecroDancer\mods\NecroDancerTrainer`。
4. 修改钻石前退出游戏，修改后重新启动游戏读取存档。

## 环境

- Windows
- Steam App ID: `247080`
- 当前针对游戏版本：`v4.2.1-b5713`

## 构建

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --add-data ".\mod;mod" --name NecroDancerTrainer_Invincible .\NecroDancerModifier.py
```

## 注意

本项目不包含《Crypt of the NecroDancer》的任何游戏本体、资源文件、存档或修改后的游戏包。使用前请自行备份游戏文件和存档。
