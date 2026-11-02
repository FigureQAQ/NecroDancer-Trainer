import json
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import NecroDancerModifier as trainer


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def subject():
    obj = SimpleNamespace(
        finder=SimpleNamespace(find=Mock(return_value=None)),
        variables={
            key: Value(False) for key in (
                "force_beat", "invincible", "one_hit", "double_damage", "free_shop",
                "dig_all", "unlock_all", "keep_coins_on_transition",
            )
        },
        status=Value(""),
        max_health_value=Value("3"), coins_value=Value("999"),
        coins_multiplier_value=Value("2"), vision_radius_value=Value("0"),
        level_value=Value("1"), item_value=Value("weapon_longsword"),
        character_value=Value("Cadence"), character_search=Value(""),
        command_id=0, coins_request_id=0, level_request_id=0,
        item_request_id=0, character_request_id=0,
        pending_requests={"levelRequest": "0", "itemRequest": "", "characterRequest": ""},
        applied_values={
            "maxHealthValue": "3", "coinsValue": "999",
            "coinsMultiplier": "2", "visionRadius": "0",
        },
    )
    for name in ("write_config", "load_config", "ensure_mod_enabled"):
        setattr(obj, name, MethodType(getattr(trainer.Trainer, name), obj))
    return obj


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mod = self.root / "local" / "mods" / "NecroDancerTrainer"
        self.config = self.mod / "config.txt"
        self.save = self.root / "NecroDancer"
        self.backup = self.save / "ModifierBackups"
        self.save.mkdir()
        self.patches = patch.multiple(
            trainer, APPDATA=self.root, MOD_DIR=self.mod, MOD_CONFIG=self.config,
            SAVE_DIR=self.save, BACKUP_DIR=self.backup,
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.obj = subject()

    def test_pending_requests_survive_other_commands_and_toggles(self):
        self.obj.write_config(item_request="torch_obsidian", item_request_id=1)
        self.obj.write_config(character_request="Cadence", character_request_id=1)
        self.obj.write_config(level_request="5", level_request_id=1)
        self.obj.write_config()
        text = self.config.read_text()
        self.assertIn("itemRequest=torch_obsidian\n", text)
        self.assertIn("characterRequest=Cadence\n", text)
        self.assertIn("levelRequest=5\n", text)

    def test_unapplied_edits_do_not_leak_into_config(self):
        self.obj.coins_multiplier_value.set("nan\ninvincible=1")
        self.obj.vision_radius_value.set("")
        self.obj.write_config()
        text = self.config.read_text()
        self.assertIn("coinsMultiplier=2\n", text)
        self.assertIn("visionRadius=0\n", text)
        self.assertEqual(text.count("invincible="), 1)

    def test_invalid_persisted_numbers_fall_back_to_valid_defaults(self):
        self.mod.mkdir(parents=True)
        self.config.write_text(
            "coinsMultiplier=nan\nvisionRadius=101\nmaxHealthValue=inf\n"
            "coinsValue=-1\nitemRequest=torch_obsidian\nitemRequestId=4\n"
        )
        self.obj.load_config()
        self.assertEqual(self.obj.coins_multiplier_value.get(), "2")
        self.assertEqual(self.obj.max_health_value.get(), "3")
        self.assertEqual(self.obj.vision_radius_value.get(), "0")
        self.assertEqual(self.obj.coins_value.get(), "999")
        self.assertEqual(self.obj.pending_requests["itemRequest"], "torch_obsidian")
        self.assertEqual(self.obj.item_request_id, 4)

    def test_repair_old_versions_backs_up_and_preserves_other_mods(self):
        text = (
            'return {\n["modLoader.list"] = {{name="Patch",package=true,version="4.2.1"},'
            '{name="NecroDancerTrainer",package=false,version="1.3.7"}},\n'
            '["progression.diamondCounter"] = 50,\n}\n'
        )
        paths = [self.save / name for name in ("SynchronyOptions.lua", "SynchronyUser_123.lua")]
        for path in paths:
            path.write_text(text)
        self.obj.ensure_mod_enabled()
        for path in paths:
            self.assertEqual(path.read_text(), text.replace("1.3.7", trainer.MOD_VERSION))
        backups = list(self.backup.glob("*.bak"))
        self.assertEqual(len(backups), 2)
        self.assertTrue(all(path.read_text() == text for path in backups))
        self.obj.ensure_mod_enabled()
        self.assertEqual(list(self.backup.glob("*.bak")), backups)

    def test_running_game_options_are_not_overwritten(self):
        path = self.save / "SynchronyOptions.lua"
        text = '["modLoader.list"] = {{name="NecroDancerTrainer",package=false,version="1.3.7"}}'
        path.write_text(text)
        self.obj.finder.find.return_value = (123, "Necrodancer.exe", Path("game.exe"))
        self.obj.ensure_mod_enabled()
        self.assertEqual(path.read_text(), text)
        self.assertFalse(self.backup.exists())
        self.obj.finder.find.return_value = None
        self.obj.ensure_mod_enabled()
        self.assertIn(trainer.MOD_VERSION, path.read_text())

    def test_live_save_edits_are_blocked(self):
        self.obj.finder.find.return_value = (123, "Necrodancer.exe", Path("game.exe"))
        self.obj.latest_save = Mock(side_effect=AssertionError("live save was accessed"))
        trainer.Trainer.write_diamonds(self.obj)
        trainer.Trainer.unlock_all_regions(self.obj)
        self.obj.latest_save.assert_not_called()

    def test_nan_multiplier_is_rejected(self):
        self.obj.coins_multiplier_value.set("nan")
        with patch.object(trainer.messagebox, "showwarning") as warning:
            trainer.Trainer.apply_coins_multiplier(self.obj)
        warning.assert_called_once()
        self.assertFalse(self.config.exists())


class ItemTests(unittest.TestCase):
    def test_reopening_item_window_focuses_entry_not_string_variable(self):
        obj = SimpleNamespace(
            item_window=Mock(), item_window_search=Value(""),
            item_window_search_entry=Mock(),
        )
        obj.item_window.winfo_exists.return_value = True
        trainer.Trainer.open_item_window(obj)
        obj.item_window_search_entry.focus_set.assert_called_once()

    def test_pixel_art_is_upscaled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "data" / "items"
            assets.mkdir(parents=True)
            Image.new("RGBA", (24, 24), "red").save(assets / "torch.png")
            obj = SimpleNamespace(
                game_dir=root, item_icon_cache={},
                item_metadata={"torch": {"image": "torch.png", "image_width": 24, "image_height": 24}},
            )
            with patch.object(trainer.ImageTk, "PhotoImage", side_effect=lambda image: image.copy()):
                image = trainer.Trainer.item_icon(obj, "torch", 52)
            self.assertEqual(image.size, (52, 52))
            self.assertEqual(image.getbbox(), (0, 0, 52, 52))

    def test_mod_version_comes_from_manifest(self):
        data = json.loads((trainer.MOD_SOURCE_DIR / "mod.json").read_text())
        self.assertEqual(trainer.MOD_VERSION, data["version"])


if __name__ == "__main__":
    unittest.main()
