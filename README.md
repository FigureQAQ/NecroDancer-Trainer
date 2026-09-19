# Crypt of the NecroDancer Trainer

一个使用 Python/Tkinter 制作的《Crypt of the NecroDancer》图形化修改器界面。

## 当前状态

- 支持检测正在运行的游戏进程。
- 支持启动 Steam 版游戏。
- 支持修改存档中的永久钻石数量，并在修改前自动备份存档。
- 无敌功能依赖本机游戏目录中已经安装的对应游戏脚本补丁，仓库和 EXE 不包含游戏文件或补丁包。
- 一击必杀、双倍伤害和金币开关目前仅完成界面交互，尚未验证实际游戏效果。

## 使用方法

1. 下载 `dist/NecroDancerTrainer_Invincible.exe`。
2. 将整个发布目录放到游戏根目录。
3. 双击 EXE，或运行 `启动修改器.bat`。
4. 修改钻石前建议先退出当前关卡，修改后重新读取存档。

## 环境

- Windows
- Steam App ID: `247080`
- 当前针对游戏版本：`v4.2.0-b5599`

## 构建

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name NecroDancerTrainer_Invincible .\NecroDancerModifier.py
```

## 注意

本项目不包含《Crypt of the NecroDancer》的任何游戏本体、资源文件、存档或修改后的游戏包。使用前请自行备份游戏文件和存档。
