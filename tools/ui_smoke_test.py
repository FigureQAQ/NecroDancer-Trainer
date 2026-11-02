"""Exercise the actual Tk windows without touching player configuration."""
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import NecroDancerModifier as module


def pump(app):
    for _ in range(8):
        app.update()
        time.sleep(0.03)


def smoke(dpi):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        mod = root / "mods" / "NecroDancerTrainer"
        with patch.multiple(
            module, APPDATA=root, LOCALAPPDATA=root, SAVE_DIR=root / "NecroDancer",
            BACKUP_DIR=root / "backups", MOD_DIR=mod, MOD_CONFIG=mod / "config.txt",
        ), patch.object(module.ProcessFinder, "find", return_value=None), \
                patch.object(module.Trainer, "register_hotkeys"), \
                patch.object(module.Trainer, "get_window_dpi", return_value=dpi):
            app = module.Trainer()
            errors = []
            app.report_callback_exception = lambda *error: errors.append(error)
            try:
                pump(app)
                app.open_item_window()
                pump(app)
                assert app.item_window_matches, "Item grid is empty"
                assert app.item_window_grid.winfo_height() > 1, "Item grid has no height"
                app.open_item_window()
                pump(app)
                app.item_window_search.set("\u9ed1")
                app.item_window_search_entry.event_generate("<KeyRelease>", keysym="a")
                pump(app)
                assert any(item == "torch_obsidian" for item, _ in app.item_window_matches)
                app.show_item_tooltip("torch_obsidian", 100, 100)
                pump(app)
                assert app.item_tooltip and app.item_tooltip.winfo_exists()
                app.item_window_search_entry.focus_force()
                app.item_window_search_entry.event_generate("<Escape>")
                pump(app)
                assert app.item_window is None, "Escape did not close the item window"
                assert app.winfo_exists(), "Closing item window also closed the trainer"
                app.open_item_window()
                pump(app)
                app.close_item_window()
                assert not errors, errors
                print(f"UI smoke passed at {dpi} DPI ({len(app.item_catalog)} catalog items)")
            finally:
                app.close()


if __name__ == "__main__":
    smoke(96)
    smoke(144)
