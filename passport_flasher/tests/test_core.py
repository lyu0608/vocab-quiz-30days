"""flasher_core 的單元測試：python3 -m pytest tests 或 python3 tests/test_core.py"""
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flasher_core as core  # noqa: E402
from flasher_core import FlashError  # noqa: E402


def image_header(chip_id=5, size=0x100):
    """最小的 ESP 映像檔：magic 0xE9、chip id 在 byte 12。"""
    h = bytearray(24)
    h[0] = 0xE9
    h[1] = 1
    struct.pack_into("<H", h, 12, chip_id)
    return bytes(h) + b"\0" * (size - 24)


def pt_entry(name, ptype, sub, off, size):
    e = bytearray(32)
    e[0:2] = b"\xaa\x50"
    e[2], e[3] = ptype, sub
    struct.pack_into("<II", e, 4, off, size)
    e[12:12 + len(name)] = name.encode()
    return bytes(e)


DEFAULT_PT = (pt_entry("nvs", 1, 2, 0x9000, 0x6000) + pt_entry("phy_init", 1, 1, 0xF000, 0x1000)
              + pt_entry("factory", 0, 0, 0x10000, 0x7F0000) + b"\xff" * 32)
HAKKA_PT = (pt_entry("nvs", 1, 2, 0x9000, 0x6000) + pt_entry("otadata", 1, 0, 0xF000, 0x2000)
            + pt_entry("phy_init", 1, 1, 0x11000, 0x1000) + pt_entry("ota_0", 0, 0x10, 0x20000, 0x280000)
            + pt_entry("ota_1", 0, 0x11, 0x2A0000, 0x280000) + pt_entry("audio", 1, 0x40, 0x520000, 0x2E0000)
            + b"\xff" * 32)


def merged_image(chip_id=5, app_size=0x20000, pt=DEFAULT_PT):
    data = bytearray(b"\xff" * (0x10000 + app_size))
    data[0:0x100] = image_header(chip_id)
    data[0x8000:0x8000 + len(pt)] = pt
    data[0x10000:0x10100] = image_header(chip_id)
    return bytes(data)


class Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def write(self, name, data):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class InspectTests(Tmp):
    def test_merged(self):
        info = core.inspect_image(self.write("full.bin", merged_image()))
        self.assertEqual(info.kind, "merged")
        self.assertEqual(info.chip_id, 5)
        self.assertEqual([p.name for p in info.partitions], ["nvs", "phy_init", "factory"])

    def test_app(self):
        info = core.inspect_image(self.write("app.bin", image_header(5, 0x5000)))
        self.assertEqual(info.kind, "app")

    def test_unknown(self):
        self.assertEqual(core.inspect_image(self.write("x.bin", b"hello" * 100)).kind, "unknown")


class SingleBinPlanTests(Tmp):
    def test_auto_merged_goes_to_zero_and_resets_settings(self):
        plan = core.plan_single_bin(self.write("full.bin", merged_image()))
        self.assertEqual([i.offset for i in plan.items], [0])
        self.assertTrue(plan.resets_settings)
        self.assertEqual(plan.flash_args, core.KEEP_FLASH_ARGS)

    def test_auto_app_goes_to_0x10000_and_keeps_settings(self):
        plan = core.plan_single_bin(self.write("app.bin", image_header(5, 0x5000)))
        self.assertEqual(plan.items[0].offset, 0x10000)
        self.assertFalse(plan.resets_settings)

    def test_wrong_chip_rejected(self):
        with self.assertRaisesRegex(FlashError, "其他晶片"):
            core.plan_single_bin(self.write("s3.bin", merged_image(chip_id=9)))

    def test_app_at_zero_rejected(self):
        with self.assertRaisesRegex(FlashError, "無法開機"):
            core.plan_single_bin(self.write("app.bin", image_header(5, 0x5000)), "full")

    def test_merged_as_app_rejected(self):
        with self.assertRaises(FlashError):
            core.plan_single_bin(self.write("full.bin", merged_image()), "app")

    def test_too_big_rejected(self):
        with self.assertRaisesRegex(FlashError, "8 MB"):
            core.plan_single_bin(self.write("big.bin", image_header(5, 0x7F0001)), "app")

    def test_custom_offset_alignment(self):
        p = self.write("x.bin", b"\x00" * 64)
        with self.assertRaisesRegex(FlashError, "對齊"):
            core.plan_single_bin(p, "custom", 0x10001)
        self.assertEqual(core.plan_single_bin(p, "custom", 0x300000).items[0].offset, 0x300000)

    def test_unknown_auto_rejected(self):
        with self.assertRaisesRegex(FlashError, "自訂位址"):
            core.plan_single_bin(self.write("x.bin", b"\x00" * 64))


class IdfBuildPlanTests(Tmp):
    """模擬 hakkaataipassport 的 build/ 目錄（OTA 雙槽＋audio 分區）。"""

    def make_build(self, chip="esp32c3", extra=None):
        b = self.dir / "build"
        self.write("build/bootloader/bootloader.bin", image_header(5, 0x5000))
        self.write("build/partition_table/partition-table.bin", HAKKA_PT)
        self.write("build/ota_data_initial.bin", b"\xff" * 0x2000)
        self.write("build/hakka-passport.bin", image_header(5, 0x150000))
        self.write("assets/hakka_audio.bin", b"\x01" * 0x2C0000)
        files = {"0x0": "bootloader/bootloader.bin", "0x8000": "partition_table/partition-table.bin",
                 "0xf000": "ota_data_initial.bin", "0x20000": "hakka-passport.bin",
                 "0x520000": "../assets/hakka_audio.bin"}
        files.update(extra or {})
        (b / "flasher_args.json").write_text(json.dumps({
            "write_flash_args": ["--flash_mode", "dio", "--flash_size", "8MB", "--flash_freq", "80m"],
            "flash_settings": {"flash_mode": "dio", "flash_size": "8MB", "flash_freq": "80m"},
            "flash_files": files,
            "extra_esptool_args": {"after": "hard_reset", "before": "default_reset", "stub": True, "chip": chip},
        }))
        return b

    def test_segments_keep_nvs_and_include_audio(self):
        plan = core.plan_from_idf_build(self.make_build())
        self.assertEqual([i.offset for i in plan.items], [0x0, 0x8000, 0xF000, 0x20000, 0x520000])
        self.assertFalse(plan.resets_settings)
        self.assertEqual(plan.flash_args, ["--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "8MB"])
        self.assertIn("audio", " ".join(plan.notes))

    def test_wrong_target_rejected(self):
        with self.assertRaisesRegex(FlashError, "set-target"):
            core.plan_from_idf_build(self.make_build(chip="esp32s3"))

    def test_nvs_overwrite_detected(self):
        self.write("build/nvs.bin", b"\0" * 0x6000)
        plan = core.plan_from_idf_build(self.make_build(extra={"0x9000": "nvs.bin"}))
        self.assertTrue(plan.resets_settings)

    def test_missing_build(self):
        with self.assertRaisesRegex(FlashError, "先編譯"):
            core.plan_from_idf_build(self.dir / "nope")

    def test_merge_bin_args(self):
        b = self.make_build()
        args = core.merge_bin_args(b, b / "out.bin")
        self.assertEqual(args[:5], ["--chip", "esp32c3", "merge-bin", "-o", str(b / "out.bin")])
        self.assertEqual(args[-2], "0x520000")


class CommandTests(unittest.TestCase):
    def test_write_flash_args(self):
        plan = core.FlashPlan("t", [core.FlashItem(0, Path("/x/full.bin"))], list(core.KEEP_FLASH_ARGS), True)
        args = core.write_flash_args("/dev/cu.usbmodem1101", 460800, plan)
        self.assertEqual(args[:2], ["--chip", "esp32c3"])
        self.assertIn("write-flash", args)
        self.assertNotIn("--erase-all", args)
        self.assertEqual(args[-2:], ["0x0", "/x/full.bin"])

    def test_legacy_names(self):
        self.assertEqual(core._legacy(["--flash-mode", "keep", "write-flash", "--before", "default-reset"]),
                         ["--flash_mode", "keep", "write_flash", "--before", "default_reset"])

    def test_esptool_accepts_our_arguments(self):
        """真的叫 esptool 解析參數：應該卡在「打不開序列埠」，而不是參數錯誤。"""
        try:
            import esptool  # noqa: F401
        except ImportError:
            self.skipTest("沒有安裝 esptool")
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "full.bin"
            f.write_bytes(merged_image())
            plan = core.plan_single_bin(f)
            r = subprocess.run(core.esptool_cmd(core.write_flash_args("/dev/does-not-exist", 460800, plan)),
                               capture_output=True, text=True, timeout=60)
            out = r.stdout + r.stderr
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("No such option", out)
            self.assertNotIn("Usage:", out)
            self.assertRegex(out, r"(?i)could not open port|no such file|does-not-exist")

    def test_parse_progress_v4_and_v5(self):
        self.assertEqual(core.parse_progress("Writing at 0x00010000... (5 %)"), 5.0)
        self.assertEqual(core.parse_progress("Writing at 0x0001a000 [====>      ]  34.5% 12288/35565 bytes..."), 34.5)
        self.assertIsNone(core.parse_progress("Compressed 890480 bytes to 500000..."))

    def test_parse_chip(self):
        self.assertEqual(core.parse_chip("Chip is ESP32-C3 (QFN32) (revision v0.4)"), "ESP32-C3")
        self.assertEqual(core.parse_chip("Chip type:          ESP32-C3 (QFN32) (revision v0.4)"), "ESP32-C3")
        self.assertEqual(core.parse_chip("Detecting chip type... ESP32-C3"), "ESP32-C3")


class GithubTests(unittest.TestCase):
    def test_parse_repo(self):
        for s in ("https://github.com/lyu0608/hakkaataipassport", "https://github.com/lyu0608/hakkaataipassport.git",
                  "lyu0608/hakkaataipassport", "https://github.com/lyu0608/hakkaataipassport/tree/main/docs",
                  "git@github.com:lyu0608/hakkaataipassport.git"):
            self.assertEqual(core.parse_github_repo(s), ("lyu0608", "hakkaataipassport"), s)
        with self.assertRaises(FlashError):
            core.parse_github_repo("not a repo")

    def test_builtin_has_my_project_first(self):
        self.assertIn("hakkaataipassport", core.BUILTIN_PROJECTS[0]["url"])


class PlayTests(unittest.TestCase):
    SAMPLE = {"slug": "fc-2", "source": "community", "isOfficial": False,
              "title": {"zh": "FC 沙罗曼蛇", "en": "FC Salamander"}, "description": {"zh": "說明"},
              "author": "某人", "categoryKey": "games", "downloads": 11, "hearts": 0, "githubUrl": "",
              "thumbnail": "/api/media/plays/fc-2/thumbnail.webp", "updatedAt": "2026-11-06T01:08:59+00:00",
              "firmware": {"available": True, "size": 890480, "sha256": "ABC123", "url": "/api/download/community/fc-2",
                           "format": "esp-merged-0x0"}}

    def test_from_api(self):
        p = core.Play.from_api(self.SAMPLE)
        self.assertEqual(p.title, "FC 沙罗曼蛇")
        self.assertEqual(p.download_url, "https://ai-passport.folotoy.cn/api/download/community/fc-2")
        self.assertEqual(p.fw_sha256, "abc123")
        self.assertEqual(p.updated, "2026-11-06")
        self.assertIn("play=fc-2", p.page_url)

    def test_carriage_returns_removed(self):
        p = core.Play.from_api(dict(self.SAMPLE, description={"zh": "第一行\r\n第二行\r第三行"}))
        self.assertEqual(p.description, "第一行\n第二行\n第三行")

    def test_unknown_format_refused(self):
        d = dict(self.SAMPLE, firmware=dict(self.SAMPLE["firmware"], format="something-else"))
        with self.assertRaisesRegex(FlashError, "格式"):
            core.download_play(core.Play.from_api(d))


class DownloadTests(Tmp):
    def test_sha_mismatch_rejected(self):
        src = self.write("src.bin", b"abc" * 1000)
        with self.assertRaisesRegex(FlashError, "SHA-256"):
            core.download_file(src.as_uri(), self.dir / "out.bin", expected_sha256="0" * 64)
        self.assertFalse((self.dir / "out.bin").exists())

    def test_ok_and_cached(self):
        src = self.write("src.bin", b"abc" * 1000)
        sha = core.sha256_file(src)
        out = core.download_file(src.as_uri(), self.dir / "out.bin", expected_sha256=sha, expected_size=3000)
        self.assertEqual(out.read_bytes(), src.read_bytes())
        src.unlink()  # 已快取且雜湊相符，不會再下載
        self.assertEqual(core.download_file("file:///nonexistent", out, expected_sha256=sha), out)


class HistoryTests(Tmp):
    def setUp(self):
        super().setUp()
        self._orig = core.app_support_dir
        core.app_support_dir = lambda: self.dir

    def tearDown(self):
        core.app_support_dir = self._orig
        super().tearDown()

    def test_add_load_reflash(self):
        f = self.write("full.bin", merged_image())
        plan = core.plan_single_bin(f)
        core.add_history({"name": "A", "result": "成功", "plan": core.plan_to_dict(plan)})
        core.add_history({"name": "B", "result": "失敗", "plan": core.plan_to_dict(plan)})
        h = core.load_history()
        self.assertEqual([x["name"] for x in h], ["B", "A"])  # 最新在前
        again = core.plan_from_dict(h[0]["plan"])
        self.assertEqual([(i.offset, i.path) for i in again.items], [(0, f)])
        f.unlink()
        with self.assertRaisesRegex(FlashError, "找不到"):
            core.plan_from_dict(h[0]["plan"])
        core.clear_history()
        self.assertEqual(core.load_history(), [])

    def test_changelog_by_claude_code(self):
        self.assertIn("by Claude Code", core.read_changelog())
        self.assertIn(core.APP_VERSION, core.read_changelog())

    def test_git_log_and_version(self):
        repo = self.dir / "r"
        repo.mkdir()
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        (repo / "version.txt").write_text("1.2.3\n")
        run("add", ".")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "第一版")
        run("tag", "v1.2.3")
        log = core.git_log(repo)
        self.assertEqual(log[0]["subject"], "第一版")
        self.assertEqual(log[0]["tags"], ["v1.2.3"])
        self.assertEqual(core.project_version(repo), "v1.2.3 · v1.2.3")


class BackupTests(Tmp):
    def setUp(self):
        super().setUp()
        self._orig = core.workspace_dir
        core.workspace_dir = lambda: self.dir

    def tearDown(self):
        core.workspace_dir = self._orig
        super().tearDown()

    def full_flash(self):
        data = bytearray(b"\xff" * core.FLASH_BYTES)
        m = merged_image()
        data[:len(m)] = m
        return bytes(data)

    def test_finalize_list_restore_delete(self):
        tmp = core.new_backup_tmp()
        tmp.write_bytes(self.full_flash())
        path = core.finalize_backup(tmp, "a0:85:e3:12:34:56", "ESP32-C3", "/dev/cu.x", "手動備份")
        self.assertFalse(tmp.exists())
        self.assertIn("a085e3123456", path.name)
        b = core.list_backups()
        self.assertEqual(len(b), 1)
        self.assertTrue(b[0]["bootable"])
        self.assertEqual(b[0]["partitions"], ["nvs", "phy_init", "factory"])
        plan = core.plan_restore(path)
        self.assertEqual([(i.offset, i.size) for i in plan.items], [(0, core.FLASH_BYTES)])
        self.assertIn("備份當時", plan.summary())
        core.delete_backup(path)
        self.assertEqual(core.list_backups(), [])

    def test_incomplete_backup_rejected(self):
        tmp = core.new_backup_tmp()
        tmp.write_bytes(b"\xff" * 1000)
        with self.assertRaisesRegex(FlashError, "不完整"):
            core.finalize_backup(tmp)
        self.assertFalse(tmp.exists())

    def test_blank_backup_cannot_restore(self):
        tmp = core.new_backup_tmp()
        tmp.write_bytes(b"\xff" * core.FLASH_BYTES)
        path = core.finalize_backup(tmp)
        self.assertTrue(core.list_backups()[0]["blank"])
        with self.assertRaisesRegex(FlashError, "無法開機"):
            core.plan_restore(path)

    def test_delete_outside_refused(self):
        f = self.write("other.bin", b"x")
        with self.assertRaises(FlashError):
            core.delete_backup(f)
        self.assertTrue(f.exists())

    def test_read_flash_args_accepted_by_esptool(self):
        try:
            import esptool  # noqa: F401
        except ImportError:
            self.skipTest("沒有安裝 esptool")
        r = subprocess.run(core.esptool_cmd(core.read_flash_args("/dev/does-not-exist", 460800, self.dir / "o.bin")),
                           capture_output=True, text=True, timeout=60)
        out = r.stdout + r.stderr
        self.assertNotIn("Usage:", out)
        self.assertRegex(out, r"(?i)could not open port|no such file|does-not-exist")

    def test_progress_read(self):
        self.assertEqual(core.parse_progress("Reading from 0x00200000 [=====>    ]  25.0% 2.00MB/8.00MB [00:10]"), 25.0)
        self.assertEqual(core.parse_progress("1048576 (12 %)"), 12.0)


class TutorialTests(unittest.TestCase):
    def test_sections(self):
        secs = core.tutorial_sections()
        titles = [t for t, _ in secs]
        self.assertGreaterEqual(len(secs), 8)
        for want in ("連接裝置", "社區", "開源專案", "ESP-IDF", "韌體檔", "上傳紀錄", "備份", "常見問題"):
            self.assertTrue(any(want in t for t in titles), want)
        self.assertTrue(all(body.strip() for _, body in secs))

    def test_code_block_not_split(self):
        secs = core.tutorial_sections("## A\n```\n## not a heading\n```\n## B\nx\n")
        self.assertEqual([t for t, _ in secs], ["A", "B"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
