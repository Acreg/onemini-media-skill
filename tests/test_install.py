"""独立安装器的隔离测试；不联网、不使用任何真实密钥。"""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("onemini_installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
TESTS = Path(__file__).resolve().parent
ENV = installer.ENV_NAME


def bundle(extra=None, *, omit=None):
    files = {
        "SKILL.md": "# 测试技能\n",
        ENV + ".example": "# 仅本地填写\nONEMINI_API_KEY=请在本地填写创作密钥\nONEMINI_BASE_URL=https://api.onemini.top\n",
        "scripts/onemini.py": "# 真实包在公开安装阶段验收\n",
        "references/quickstart.md": "# 使用说明\n",
    }
    files.update({f"references/page-{number}.md": f"# 参数 {number}\n" for number in range(41)})
    if omit:
        files.pop(omit)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for folder in ("onemini-media/", "onemini-media/scripts/", "onemini-media/references/", "onemini-media/tests/", "onemini-media/references/images/", "onemini-media/references/videos/", "onemini-media/references/music/"):
            archive.writestr(folder, b"")
        for name, data in files.items():
            archive.writestr("onemini-media/" + name, data)
        for name, data in extra or []:
            archive.writestr(name, data)
    return stream.getvalue()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="installer-test-", dir=TESTS)
        self.root = Path(self.temp.name)
        self.parent = self.root / "中文 任务空间" / "skills 父目录"
        self.downloads = []

    def tearDown(self):
        self.temp.cleanup()

    @contextlib.contextmanager
    def download(self, data=None, *, checksum=None):
        data = bundle() if data is None else data
        def urlopen(url, timeout):
            self.downloads.append((url, timeout))
            return io.BytesIO(data)
        expected = checksum or hashlib.sha256(data).hexdigest()
        with patch.object(installer.urllib.request, "urlopen", side_effect=urlopen), patch.object(installer, "EXPECTED_SHA256", expected):
            yield

    def test_fixed_version_url_and_official_checksum(self):
        self.assertEqual(installer.VERSION, "v0.1.0")
        self.assertEqual(installer.DOWNLOAD_URL, "https://github.com/Acreg/onemini-media-skill/releases/download/v0.1.0/onemini-media-v0.1.0.zip")
        self.assertEqual(installer.EXPECTED_SHA256, "987f1f797cc4d559ca28e522e6b853f77349519578cf6df412ce5f42693b8bc8")

    def test_install_45_files_under_chinese_parent_with_spaces(self):
        with self.download():
            root = installer.install(self.parent)
        self.assertEqual(root, self.parent.resolve() / "onemini-media")
        self.assertTrue(root.is_absolute())
        self.assertEqual(len([p for p in root.rglob("*") if p.is_file()]), 46)
        self.assertTrue((root / "SKILL.md").is_file())
        self.assertTrue((root / "scripts/onemini.py").is_file())
        config = (root / ENV).read_text(encoding="utf-8")
        self.assertIn("ONEMINI_API_KEY=\n", config)
        self.assertIn("ONEMINI_BASE_URL=https://api.onemini.top", config)
        self.assertNotIn("ONEMINI_API_KEY=请", config)
        self.assertEqual(self.downloads, [(installer.DOWNLOAD_URL, 60)])
        self.assertEqual(list(self.parent.glob(".onemini-install-*")), [])

    def test_reinstall_never_reads_or_overwrites_existing_config_and_user_files(self):
        with self.download():
            root = installer.install(self.parent)
        existing = {
            ENV: b"ONEMINI_API_KEY=fixture-only-never-display\nONEMINI_BASE_URL=https://api-hk.onemini.top\n",
            ".env": b"keep alternate local config\n",
            "notes/custom.txt": "用户自己的文件".encode(),
            "outputs/song.m4a": b"existing-work",
            ".cache/tasks/original.json": b'{"task_id":"existing-task"}',
            ".cache/docs/state.json": b'{"last_checked_at":"keep"}',
        }
        for relative, content in existing.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (root / "SKILL.md").write_text("old package version", encoding="utf-8")
        original_read = Path.read_text
        def guard_read(path, *args, **kwargs):
            self.assertNotIn(path, {root / ENV, root / ".env"}, "安装器不应读取已有密钥")
            return original_read(path, *args, **kwargs)
        with self.download(), patch.object(Path, "read_text", guard_read):
            installer.install(self.parent)
        for relative, content in existing.items():
            self.assertEqual((root / relative).read_bytes(), content)
        self.assertEqual((root / "SKILL.md").read_text(encoding="utf-8"), "# 测试技能\n")

    def test_hash_mismatch_does_not_create_installation_or_overwrite_old_files(self):
        target = self.parent / installer.SKILL_NAME
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(b"keep old version")
        with self.download(checksum="0" * 64), self.assertRaises(installer.InstallError) as caught:
            installer.install(self.parent)
        self.assertIn("SHA256", str(caught.exception))
        self.assertEqual((target / "SKILL.md").read_bytes(), b"keep old version")
        self.assertFalse((target / ENV).exists())
        self.assertEqual(list(self.parent.glob(".onemini-install-*")), [])

    def test_invalid_zip_is_rejected_before_installing(self):
        with self.download(b"not a zip file"), self.assertRaises(installer.InstallError):
            installer.install(self.parent)
        self.assertFalse((self.parent / installer.SKILL_NAME).exists())
        self.assertEqual(list(self.parent.glob(".onemini-install-*")), [])

    def test_zip_traversal_absolute_and_wrong_prefix_are_rejected(self):
        for name in ("onemini-media/../escape.txt", "/absolute.txt", "wrong-prefix/SKILL.md", "onemini-media/C:escape.txt", "onemini-media/..\\escape.txt", "onemini-media/unsafe. /file.txt"):
            with self.subTest(name=name):
                with self.download(bundle([(name, b"bad")])), self.assertRaises(installer.InstallError):
                    installer.install(self.parent)
                self.assertFalse((self.parent / installer.SKILL_NAME).exists())
                self.assertFalse((self.parent / "escape.txt").exists())
                self.assertEqual(list(self.parent.glob(".onemini-install-*")), [])

    def test_archive_symlink_rejected(self):
        link = zipfile.ZipInfo("onemini-media/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.download(bundle([(link, b"../../escape")])), self.assertRaises(installer.InstallError):
            installer.install(self.parent)
        self.assertFalse((self.parent / installer.SKILL_NAME).exists())

    def test_bundle_with_real_config_or_missing_required_files_rejected(self):
        for data in (bundle([(f"onemini-media/{ENV}", b"must not install")]), bundle(omit="SKILL.md"), bundle(omit=ENV + ".example")):
            with self.download(data), self.assertRaises(installer.InstallError):
                installer.install(self.parent)
            self.assertFalse((self.parent / installer.SKILL_NAME).exists())

    def test_case_colliding_archive_paths_rejected(self):
        with self.download(bundle([("onemini-media/skill.md", b"collision")])), self.assertRaises(installer.InstallError):
            installer.install(self.parent)
        self.assertFalse((self.parent / installer.SKILL_NAME).exists())

    def test_existing_link_cannot_redirect_install_writes(self):
        root = self.parent / installer.SKILL_NAME
        root.mkdir(parents=True)
        outside = self.root / "keep-outside"
        outside.mkdir()
        try:
            (root / "scripts").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("当前 Windows 未授予创建符号链接权限")
        with self.download(), self.assertRaises(installer.InstallError):
            installer.install(self.parent)
        self.assertFalse((outside / "onemini.py").exists())
        self.assertFalse((root / "SKILL.md").exists())

    def test_existing_directory_conflict_fails_before_overwriting_package_files(self):
        root = self.parent / installer.SKILL_NAME
        (root / "scripts/onemini.py").mkdir(parents=True)
        (root / "SKILL.md").write_bytes(b"original")
        with self.download(), self.assertRaises(installer.InstallError):
            installer.install(self.parent)
        self.assertEqual((root / "SKILL.md").read_bytes(), b"original")

    def test_network_failure_is_not_retried(self):
        with patch.object(installer.urllib.request, "urlopen", side_effect=urllib.error.URLError("fixture network unavailable")) as request:
            with self.assertRaises(installer.InstallError) as caught:
                installer.install(self.parent)
        self.assertIn("下载", str(caught.exception))
        self.assertEqual(request.call_count, 1)
        self.assertFalse((self.parent / installer.SKILL_NAME).exists())
        self.assertEqual(list(self.parent.glob(".onemini-install-*")), [])

    def test_main_success_prints_version_absolute_paths_reload_without_key_contents(self):
        output, errors = io.StringIO(), io.StringIO()
        with self.download(), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = installer.main(["--目录", str(self.parent)])
        self.assertEqual(result, 0)
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("v0.1.0", output.getvalue())
        self.assertIn(str(self.parent.resolve() / installer.SKILL_NAME), output.getvalue())
        self.assertIn(ENV, output.getvalue())
        self.assertIn("重新加载", output.getvalue())
        self.assertNotIn("ONEMINI_API_KEY=", output.getvalue())

    def test_missing_parent_argument_is_chinese_error_without_network(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), patch.object(installer.urllib.request, "urlopen") as request:
            result = installer.main([])
        self.assertNotEqual(result, 0)
        self.assertIn("--目录", errors.getvalue())
        self.assertIn("安装失败", errors.getvalue())
        request.assert_not_called()

    def test_standalone_help_and_error_are_strict_utf8_under_gbk_environment(self):
        standalone = self.root / "独立 脚本" / "install-onemini.py"
        standalone.parent.mkdir()
        shutil.copyfile(ROOT / "install.py", standalone)
        environment = {**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONDONTWRITEBYTECODE": "1"}
        for arguments, expected in ((["--help"], 0), ([], 1)):
            result = subprocess.run([sys.executable, str(standalone), *arguments], cwd=self.root, capture_output=True, env=environment, check=False)
            self.assertEqual(result.returncode, expected)
            stdout = result.stdout.decode("utf-8", errors="strict")
            stderr = result.stderr.decode("utf-8", errors="strict")
            self.assertIn("用法" if expected == 0 else "安装失败", stdout if expected == 0 else stderr)
            self.assertNotIn("show this help", stdout)


if __name__ == "__main__":
    unittest.main()
