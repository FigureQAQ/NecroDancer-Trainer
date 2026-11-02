import ctypes
import json
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import NecroDancerModifier as trainer


ITEM_CATALOG_FIXTURE = Path(__file__).with_name("fixtures") / "item_catalog_ids.txt"
BROAD_ITEM_NAMES = {
    "游戏道具", "护甲", "火把", "靴", "武器", "食物", "铲子", "法术",
    "戒指", "卷轴", "法典", "使魔", "袋", "资源", "永久", "心脏",
    "黄金", "玻璃", "王冠", "盾牌",
}


def fixture_item_ids():
    return [
        line.strip()
        for line in ITEM_CATALOG_FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def broad_generated_names(item_ids):
    result = {}
    for item_id in item_ids:
        name = trainer.Trainer.item_name_in_chinese(item_id, item_id)
        if name in BROAD_ITEM_NAMES:
            result[item_id] = name
    return result


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
        diamond_value=Value(""),
        diamond_account="",
        save_account_invalidated=False,
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
    for name in (
        "write_config", "load_config", "ensure_mod_enabled",
        "game_is_running", "require_game_stopped",
    ):
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

    def test_loader_repair_preserves_crlf_and_backs_up_exact_bytes(self):
        path = self.save / "SynchronyOptions.lua"
        original = (
            b'return {\r\n["modLoader.list"] = '
            b'{{name="NecroDancerTrainer",package=false,version="1.3.7"}},\r\n}\r\n'
        )
        path.write_bytes(original)

        self.obj.ensure_mod_enabled()

        expected = original.replace(b"1.3.7", trainer.MOD_VERSION.encode())
        backup = next(self.backup.glob("*.bak"))
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(backup.read_bytes(), original)

    def test_loader_replace_failure_preserves_original_and_cleans_temp(self):
        path = self.save / "SynchronyOptions.lua"
        original = (
            b'["modLoader.list"] = '
            b'{{name="NecroDancerTrainer",package=false,version="1.3.7"}}'
        )
        path.write_bytes(original)

        with patch.object(trainer.os, "replace", side_effect=OSError("replace denied")):
            with self.assertRaises(OSError):
                self.obj.ensure_mod_enabled()

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.save.glob(f".{path.name}.*.tmp")), [])
        self.assertEqual(len(list(self.backup.glob("*.bak"))), 1)

    def test_repeated_loader_replace_failure_reuses_identical_backup(self):
        path = self.save / "SynchronyOptions.lua"
        path.write_bytes(
            b'["modLoader.list"] = '
            b'{{name="NecroDancerTrainer",package=false,version="1.3.7"}}'
        )
        with patch.object(trainer.os, "replace", side_effect=OSError("replace denied")):
            for _ in range(3):
                with self.assertRaises(OSError):
                    self.obj.ensure_mod_enabled()
        self.assertEqual(len(list(self.backup.glob("*.bak"))), 1)

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

    def test_mod_entry_matching_allows_spacing_and_field_order(self):
        path = self.save / "SynchronyOptions.lua"
        path.write_text(
            '["modLoader.list"] = {{ version = "1.3.7", package = false, '
            'name = "NecroDancerTrainer" }}'
        )
        self.obj.ensure_mod_enabled()
        updated = path.read_text()
        self.assertIn(f'version = "{trainer.MOD_VERSION}"', updated)
        self.assertIn('name = "NecroDancerTrainer"', updated)

    def test_live_save_edits_are_blocked(self):
        self.obj.finder.find.return_value = (123, "Necrodancer.exe", Path("game.exe"))
        self.obj.latest_save = Mock(side_effect=AssertionError("live save was accessed"))
        trainer.Trainer.write_diamonds(self.obj)
        trainer.Trainer.unlock_all_regions(self.obj)
        self.obj.latest_save.assert_not_called()

    def test_multiple_save_accounts_require_explicit_selection(self):
        (self.save / "SynchronyUser_111.lua").write_text("first")
        (self.save / "SynchronyUser_222.lua").write_text("second")
        self.obj.save_account = Value("")
        self.obj.save_account_combo = None
        self.obj.save_files = MethodType(trainer.Trainer.save_files, self.obj)
        self.obj.refresh_save_accounts = MethodType(trainer.Trainer.refresh_save_accounts, self.obj)
        self.obj.latest_save = MethodType(trainer.Trainer.latest_save, self.obj)

        self.assertIsNone(self.obj.latest_save())
        self.obj.save_account.set("SynchronyUser_222.lua")
        self.assertEqual(self.obj.latest_save().name, "SynchronyUser_222.lua")

    def test_diamond_refresh_accepts_tk_event_and_loads_selected_account(self):
        (self.save / "SynchronyUser_111.lua").write_text(
            '["progression.diamondCounter"] = 11\n'
        )
        (self.save / "SynchronyUser_222.lua").write_text(
            '["progression.diamondCounter"] = 22\n'
        )
        self.obj.save_account = Value("SynchronyUser_222.lua")
        self.obj.diamond_value = Value("11")
        self.obj.diamond_account = "SynchronyUser_111.lua"
        self.obj.save_account_combo = None
        self.obj.save_files = MethodType(trainer.Trainer.save_files, self.obj)
        self.obj.refresh_save_accounts = MethodType(
            trainer.Trainer.refresh_save_accounts, self.obj
        )
        self.obj.latest_save = MethodType(trainer.Trainer.latest_save, self.obj)

        trainer.Trainer.refresh_diamonds(self.obj, object())

        self.assertEqual(self.obj.diamond_value.get(), "22")
        self.assertEqual(self.obj.diamond_account, "SynchronyUser_222.lua")

    def test_removed_selected_account_does_not_fall_back_to_another_account(self):
        (self.save / "SynchronyUser_111.lua").write_text("first")
        removed = self.save / "SynchronyUser_222.lua"
        removed.write_text("second")
        self.obj.save_account = Value(removed.name)
        self.obj.diamond_value = Value("222")
        self.obj.diamond_account = removed.name
        self.obj.save_account_combo = None
        self.obj.save_files = MethodType(trainer.Trainer.save_files, self.obj)
        self.obj.refresh_save_accounts = MethodType(
            trainer.Trainer.refresh_save_accounts, self.obj
        )
        self.obj.latest_save = MethodType(trainer.Trainer.latest_save, self.obj)
        removed.unlink()

        self.obj.refresh_save_accounts()

        self.assertEqual(self.obj.save_account.get(), "")
        self.assertEqual(self.obj.diamond_value.get(), "")
        self.assertEqual(self.obj.diamond_account, "")
        self.assertIsNone(self.obj.latest_save())

    def test_diamond_refresh_clears_stale_value_when_field_is_missing(self):
        path = self.save / "SynchronyUser_123.lua"
        path.write_text("no diamond field")
        self.obj.save_account = Value(path.name)
        self.obj.diamond_value = Value("77")
        self.obj.diamond_account = path.name
        self.obj.save_account_combo = None
        self.obj.save_files = MethodType(trainer.Trainer.save_files, self.obj)
        self.obj.refresh_save_accounts = MethodType(
            trainer.Trainer.refresh_save_accounts, self.obj
        )
        self.obj.latest_save = MethodType(trainer.Trainer.latest_save, self.obj)

        trainer.Trainer.refresh_diamonds(self.obj, object())

        self.assertEqual(self.obj.diamond_value.get(), "")
        self.assertEqual(self.obj.diamond_account, "")
        self.assertEqual(self.obj.status.get(), "存档中没有找到钻石字段")

    def test_diamond_write_refuses_value_loaded_from_another_account(self):
        path = self.save / "SynchronyUser_222.lua"
        original = b'["progression.diamondCounter"] = 22\n'
        path.write_bytes(original)
        obj = SimpleNamespace(
            finder=SimpleNamespace(find=Mock(return_value=None)),
            latest_save=Mock(return_value=path),
            diamond_value=Value("11"),
            diamond_account="SynchronyUser_111.lua",
            status=Value(""),
        )
        obj.game_is_running = MethodType(trainer.Trainer.game_is_running, obj)

        trainer.Trainer.write_diamonds(obj)

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(obj.diamond_value.get(), "")
        self.assertIn("未从当前账号加载", obj.status.get())

    def test_region_unlock_does_not_corrupt_unlock_type_sequence(self):
        path = self.save / "SynchronyUser_123.lua"
        original_unlocks = (
            '["progression.unlocks.user"] = {{Beastmaster=true},{ArmorChainmail=true}},\n'
        )
        path.write_text(
            'NumZone1Completions=0\nNumZone2Completions=0\n'
            'NumZone3Completions=0\nNumZone4Completions=0\n'
            'NumZone5Completions=0\n' + original_unlocks
        )
        self.obj.save_account = Value("SynchronyUser_123.lua")
        self.obj.save_account_combo = None
        self.obj.save_files = MethodType(trainer.Trainer.save_files, self.obj)
        self.obj.refresh_save_accounts = MethodType(trainer.Trainer.refresh_save_accounts, self.obj)
        self.obj.latest_save = MethodType(trainer.Trainer.latest_save, self.obj)

        trainer.Trainer.unlock_all_regions(self.obj)

        updated = path.read_text()
        self.assertIn(original_unlocks, updated)
        self.assertNotIn("Aria=true", updated)
        self.assertIn("NumZone5Completions=9999", updated)

    def test_save_backup_names_do_not_overwrite_same_second(self):
        path = self.save / "SynchronyUser_123.lua"
        first = b"first save"
        second = b"second save"
        path.write_bytes(first)
        with patch.object(trainer.time, "strftime", return_value="20261102-120000"):
            first_backup = trainer.backup_and_replace_save(
                path, first, "updated one", "before_diamonds"
            )
            second_backup = trainer.backup_and_replace_save(
                path, second, "updated two", "before_diamonds"
            )

        self.assertNotEqual(first_backup, second_backup)
        self.assertEqual(first_backup.read_bytes(), first)
        self.assertEqual(second_backup.read_bytes(), second)
        self.assertEqual(path.read_bytes(), b"updated two")

    def test_save_replace_failure_keeps_original_and_reports_error(self):
        path = self.save / "SynchronyUser_123.lua"
        original = b'["progression.diamondCounter"] = 10\n'
        path.write_bytes(original)
        obj = SimpleNamespace(
            finder=SimpleNamespace(find=Mock(return_value=None)),
            latest_save=Mock(return_value=path),
            diamond_value=Value("25"),
            diamond_account=path.name,
            status=Value(""),
        )
        obj.game_is_running = MethodType(trainer.Trainer.game_is_running, obj)
        obj.require_game_stopped = MethodType(
            trainer.Trainer.require_game_stopped, obj
        )

        with patch.object(trainer.os, "replace", side_effect=OSError("replace denied")):
            trainer.Trainer.write_diamonds(obj)

        self.assertEqual(path.read_bytes(), original)
        self.assertIn("原存档未替换", obj.status.get())
        self.assertEqual(len(list(self.backup.glob("*.bak"))), 1)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_backup_failure_keeps_original(self):
        path = self.save / "SynchronyUser_123.lua"
        original = b'["progression.diamondCounter"] = 10\n'
        path.write_bytes(original)
        self.backup.write_text("not a directory")

        with self.assertRaises(OSError):
            trainer.backup_and_replace_save(
                path, original, '["progression.diamondCounter"] = 25\n',
                "before_diamonds",
            )

        self.assertEqual(path.read_bytes(), original)

    def test_region_unlock_reports_replace_failure_without_changing_save(self):
        path = self.save / "SynchronyUser_123.lua"
        original = (
            "NumZone1Completions=0\nNumZone2Completions=0\n"
            "NumZone3Completions=0\nNumZone4Completions=0\n"
            "NumZone5Completions=0\n"
        ).encode()
        path.write_bytes(original)
        obj = SimpleNamespace(
            finder=SimpleNamespace(find=Mock(return_value=None)),
            latest_save=Mock(return_value=path),
            status=Value(""),
        )
        obj.game_is_running = MethodType(trainer.Trainer.game_is_running, obj)
        obj.require_game_stopped = MethodType(
            trainer.Trainer.require_game_stopped, obj
        )

        with patch.object(trainer.os, "replace", side_effect=OSError("replace denied")):
            trainer.Trainer.unlock_all_regions(obj)

        self.assertEqual(path.read_bytes(), original)
        self.assertIn("原存档未替换", obj.status.get())

    def test_game_directory_change_rebuilds_catalog_and_metadata(self):
        obj = SimpleNamespace(
            game_dir=None, item_metadata={"old": {}}, item_icon_cache={"old": object()},
            item_catalog=[], character_catalog=[],
            load_item_catalog=Mock(return_value=[("new_item", "新物品")]),
            load_character_catalog=Mock(return_value=[("NewCharacter", "新角色")]),
        )
        trainer.Trainer.set_game_dir(obj, Path("game"))
        self.assertEqual(obj.item_catalog, [("new_item", "新物品")])
        self.assertEqual(obj.character_catalog, [("NewCharacter", "新角色")])
        self.assertEqual(obj.item_metadata, {})
        self.assertEqual(obj.item_icon_cache, {})

    def test_nan_multiplier_is_rejected(self):
        self.obj.coins_multiplier_value.set("nan")
        with patch.object(trainer.messagebox, "showwarning") as warning:
            trainer.Trainer.apply_coins_multiplier(self.obj)
        warning.assert_called_once()
        self.assertFalse(self.config.exists())


class ProcessFinderTests(unittest.TestCase):
    def test_matching_process_with_inaccessible_path_is_still_reported(self):
        class Kernel:
            def CreateToolhelp32Snapshot(self, _flags, _pid):
                return 1

            def Process32FirstW(self, _snapshot, entry_pointer):
                entry = ctypes.cast(
                    entry_pointer, ctypes.POINTER(trainer.PROCESSENTRY32W)
                ).contents
                entry.th32ProcessID = 456
                entry.szExeFile = "Necrodancer64.exe"
                return 1

            def Process32NextW(self, _snapshot, _entry_pointer):
                return 0

            def CloseHandle(self, _handle):
                return 1

            def OpenProcess(self, _access, _inherit, _pid):
                return 0

            def GetLastError(self):
                return 5

        finder = trainer.ProcessFinder.__new__(trainer.ProcessFinder)
        finder.kernel32 = Kernel()

        self.assertEqual(finder.find(), (456, "Necrodancer64.exe", None))
        self.assertTrue(finder.is_running())

    def test_process_scan_failure_is_treated_as_running(self):
        class Kernel:
            def CreateToolhelp32Snapshot(self, _flags, _pid):
                return 0

        finder = trainer.ProcessFinder.__new__(trainer.ProcessFinder)
        finder.kernel32 = Kernel()

        self.assertTrue(finder.is_running())

    def test_game_start_before_atomic_replace_cancels_write(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        path = root / "save.lua"
        original = b"original"
        path.write_bytes(original)

        with patch.object(trainer, "BACKUP_DIR", root / "backups"):
            with self.assertRaises(trainer.GameRunningError):
                trainer.backup_and_replace_save(
                    path,
                    original,
                    "updated",
                    "before_test",
                    before_replace=lambda: (_ for _ in ()).throw(
                        trainer.GameRunningError("game started")
                    ),
                )

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])


class ItemSearchTests(unittest.TestCase):
    def setUp(self):
        self.item_catalog = [
            ("armor_heavyglass", "armor_heavyglass"),
            ("torch_foresight", "torch_foresight"),
            ("spell_transform", "spell_transform"),
            ("head_glass_jaw", "head_glass_jaw"),
            ("armor_obsidian", "黑曜石护甲"),
            ("head_miners_cap", "head_miners_cap"),
            ("feet_boots_explorers", "feet_boots_explorers"),
            ("feet_boots_lead", "feet_boots_lead"),
        ]
        self.character_catalog = [("Cadence", "卡登斯")]
        self.obj = SimpleNamespace(
            item_catalog=self.item_catalog,
            character_catalog=self.character_catalog,
            item_aliases=trainer.Trainer.load_item_aliases(),
            character_aliases=trainer.Trainer.load_character_aliases(),
            item_name_in_chinese=trainer.Trainer.item_name_in_chinese,
        )
        self.obj.catalog_aliases = MethodType(trainer.Trainer.catalog_aliases, self.obj)

    def test_full_catalog_fixture_has_no_broad_generated_names(self):
        item_ids = fixture_item_ids()
        self.assertEqual(len(item_ids), 291)
        self.assertEqual(broad_generated_names(item_ids), {})

    def test_catalog_audit_flags_unknown_ids_that_fall_back(self):
        self.assertEqual(
            broad_generated_names(["weapon_unknown_relic"]),
            {"weapon_unknown_relic": "武器"},
        )

    def test_new_priority_names_take_precedence_over_generic_words(self):
        expected = {
            "feet_boots_explorers": "探险靴",
            "feet_boots_lunging": "突进靴",
            "scroll_earthquake": "地震卷轴",
            "ring_charisma": "魅力戒指",
            "tome_shield": "护盾法典",
            "familiar_shopkeeper": "店主使魔",
            "food_magic_2": "魔法奶酪",
            "weapon_cat": "九尾鞭",
            "weapon_blunderbuss": "短铳",
        }
        aliases = trainer.Trainer.load_item_aliases()
        catalog = [
            (item_id, aliases.get(item_id, (item_id,))[0])
            for item_id in fixture_item_ids()
        ]
        for item_id, name in expected.items():
            with self.subTest(item_id=item_id):
                self.assertEqual(
                    trainer.Trainer.item_name_in_chinese(item_id, item_id), name
                )
                self.assertEqual(
                    trainer.Trainer.resolve_catalog_value(name, catalog, aliases),
                    item_id,
                )

    def test_duplicate_catalog_names_are_not_silently_resolved(self):
        aliases = trainer.Trainer.load_item_aliases()
        catalog = [
            (item_id, aliases.get(item_id, (item_id,))[0])
            for item_id in fixture_item_ids()
        ]
        internals = {item_id for item_id, _display in catalog}
        for query in ("金币堆", "金钥匙"):
            with self.subTest(query=query):
                resolved = trainer.Trainer.resolve_catalog_value(
                    query, catalog, aliases
                )
                self.assertEqual(resolved, query)
                self.assertNotIn(resolved, internals)

    def test_generated_names_distinguish_known_item_combinations(self):
        expected = {
            "armor_heavyglass": "重型玻璃护甲",
            "torch_foresight": "预见火把",
            "spell_transform": "变身法术",
            "head_glass_jaw": "玻璃下巴",
        }
        generated = {
            item_id: trainer.Trainer.item_name_in_chinese(item_id, item_id)
            for item_id in expected
        }
        self.assertEqual(generated, expected)
        self.assertEqual(len(set(generated.values())), len(expected))

    def test_item_search_matches_full_display_and_generated_chinese_names(self):
        expected = {
            "重型玻璃护甲": "armor_heavyglass",
            "预见火把": "torch_foresight",
            "变身法术": "spell_transform",
            "玻璃下巴": "head_glass_jaw",
            "黑曜石护甲": "armor_obsidian",
            "黑曜石甲": "armor_obsidian",
        }
        for query, item_id in expected.items():
            matches = trainer.Trainer.catalog_matches(
                self.obj, query, self.item_catalog
            )
            self.assertEqual(
                [internal for internal, _display in matches], [item_id], query
            )

    def test_character_search_does_not_use_item_name_fallbacks(self):
        matches = trainer.Trainer.catalog_matches(
            self.obj, "游戏道具", self.character_catalog
        )
        self.assertEqual(matches, [])

    def test_resolve_catalog_value_accepts_unique_generated_name(self):
        self.assertEqual(
            trainer.Trainer.resolve_catalog_value(
                "矿工帽", self.item_catalog, self.obj.item_aliases
            ),
            "head_miners_cap",
        )

    def test_resolve_catalog_value_keeps_ambiguous_generated_name_as_input(self):
        self.assertEqual(
            trainer.Trainer.resolve_catalog_value(
                "靴", self.item_catalog, self.obj.item_aliases
            ),
            "靴",
        )

    def test_apply_item_uses_unique_generated_name(self):
        obj = SimpleNamespace(
            item_value=Value("矿工帽"),
            item_catalog=self.item_catalog,
            item_aliases=self.obj.item_aliases,
            resolve_catalog_value=trainer.Trainer.resolve_catalog_value,
            request_item=Mock(),
        )
        with patch.object(trainer.messagebox, "showwarning") as warning:
            trainer.Trainer.apply_item(obj)
        warning.assert_not_called()
        obj.request_item.assert_called_once_with("head_miners_cap")

    def test_apply_item_keeps_ambiguous_generated_name_as_input(self):
        obj = SimpleNamespace(
            item_value=Value("靴"),
            item_catalog=self.item_catalog,
            item_aliases=self.obj.item_aliases,
            resolve_catalog_value=trainer.Trainer.resolve_catalog_value,
            request_item=Mock(),
        )
        with patch.object(trainer.messagebox, "showwarning") as warning:
            trainer.Trainer.apply_item(obj)
        warning.assert_not_called()
        obj.request_item.assert_called_once_with("靴")

    def test_apply_character_does_not_resolve_generated_item_fallback(self):
        obj = SimpleNamespace(
            character_search=Value("游戏道具"),
            character_value=Value(""),
            character_catalog=[("Cadence", "Cadence")],
            character_aliases={},
            resolve_catalog_value=trainer.Trainer.resolve_catalog_value,
            command_id=0,
            character_request_id=0,
            write_config=Mock(),
            status=Value(""),
        )
        with patch.object(trainer.messagebox, "showwarning") as warning:
            trainer.Trainer.apply_character(obj)
        warning.assert_called_once()
        obj.write_config.assert_not_called()

    def test_character_resolution_keeps_existing_id_display_and_alias_matches(self):
        expected = {"Cadence": "Cadence", "卡登斯": "Cadence"}
        for query, internal in expected.items():
            self.assertEqual(
                trainer.Trainer.resolve_catalog_value(
                    query, self.character_catalog, self.obj.character_aliases
                ),
                internal,
            )


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
