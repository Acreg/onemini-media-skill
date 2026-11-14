"""文档检查与事务更新测试；全部联网响应由正式结构的隔离字节夹具提供。"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_docs as docs

TESTS = Path(__file__).resolve().parent
NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)


def fixture():
    definitions = [("9683548m0", "任务查询", "common", []), ("9683549m0", "模型总览", "common", []),
                   ("100m0", "图片家族", "image", ["image-exact"]), ("101m0", "视频家族", "video", ["video-exact"]), ("102m0", "音乐家族", "music", ["music-exact", "music-two"])]
    pages, lines, bodies = [], [], {}
    labels = {"common": "", "image": " > 图片模型 API", "video": " > 视频模型 API", "music": " > 音乐模型 API"}
    overview = ["# 总览", "", "| 调用文档 | 数量 | 精确模型 ID |", "|---|---|---|"]
    for page_id, title, kind, ids in definitions:
        url = docs.ORIGIN + "/" + page_id + ".md"
        path = docs.COMMON.get(page_id, f"references/{docs.GROUPS.get(kind, 'common')}/{page_id}.md")
        lines.append(f"- 多媒体 API（图片、视频、音乐）{labels[kind]} [{title}]({url}): ")
        body = ("# " + title + "\n\n" + "、".join("`" + m + "`" for m in ids) + "\n").encode()
        if kind != "common":
            overview.append(f"| [{title}]({url.removesuffix('.md')}) | {len(ids)} | " + "、".join("`" + m + "`" for m in ids) + " |")
        bodies[url] = body
        pages.append({"id": page_id, "title": title, "kind": kind, "url": url, "path": path, "sha256": docs.digest(body), "model_ids": ids})
    bodies[docs.ORIGIN + "/9683549m0.md"] = ("\n".join(overview) + "\n").encode()
    for page in pages:
        page["sha256"] = docs.digest(bodies[page["url"]])
    index = ("# 网站索引\n- 聊天 [指南](https://docs-api.onemini.top/42m0.md)\n" + "\n".join(lines) + "\n").encode()
    bodies[docs.INDEX] = index
    manifest = {"schema_version": 1, "snapshot_at": (NOW - timedelta(days=8)).isoformat(), "source_index": docs.INDEX,
                "source_index_sha256": docs.digest(index), "check_interval_days": 7, "pages": pages}
    return manifest, bodies


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TESTS)
        self.root = Path(self.temp.name)
        self.manifest, self.remote = fixture()
        docs.write_json(self.root / "docs-manifest.json", self.manifest)
        for page in self.manifest["pages"]:
            target = self.root / page["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.remote[page["url"]])
        (self.root / "references" / "models.md").write_bytes(docs.models_markdown(self.manifest))
        for file, content in (("SKILL.md", b"keep skill"), ("scripts/onemini.py", b"keep program"), ("references/errors.md", b"keep errors"), ("references/install.md", b"keep local guide"), ("secret.env", b"keep secret")):
            path = self.root / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.sync = docs.Syncer(self.root, now=NOW)
        self.calls = []
        def fetch(url):
            docs.document_url(url)
            self.calls.append(url)
            return self.remote[url]
        self.fetch_patch = patch.object(docs, "fetch", side_effect=fetch)
        self.fetch_patch.start()

    def tearDown(self):
        self.fetch_patch.stop()
        self.temp.cleanup()

    def installed(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob("*") if path.is_file() and ".cache" not in path.relative_to(self.root).parts}

    def change_body(self):
        url = docs.ORIGIN + "/100m0.md"
        self.remote[url] += "\n新参数：保持精确字段。\n".encode()

    def test_installed_snapshot_ignores_only_root_relative_cache(self):
        original = self.root
        nested = original / ".cache" / "nested-install"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_bytes(b"installed skill")
        (nested / ".cache").mkdir()
        (nested / ".cache" / "state.json").write_bytes(b"ignored runtime")
        self.root = nested
        try:
            self.assertEqual(self.installed(), {"SKILL.md": b"installed skill"})
        finally:
            self.root = original

    def test_not_due_no_network(self):
        self.manifest["snapshot_at"] = NOW.isoformat()
        docs.write_json(self.root / "docs-manifest.json", self.manifest)
        self.assertEqual(self.sync.check()["status"], "not_due")
        self.assertEqual(self.calls, [])

    def test_due_unchanged_fetches_every_body_and_not_again(self):
        before = self.installed()
        result = self.sync.check()
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(len(self.calls), len(self.manifest["pages"]) + 1)
        self.assertEqual(self.installed(), before)
        self.calls.clear()
        self.assertEqual(self.sync.check()["status"], "not_due")
        self.assertEqual(self.calls, [])

    def test_body_only_change_detected_index_identical(self):
        self.change_body()
        before = self.installed()
        result = self.sync.check()
        self.assertEqual(result["status"], "pending_update")
        self.assertEqual(result["changes"][0]["id"], "100m0")
        self.assertEqual(self.installed(), before)
        self.calls.clear()
        self.assertEqual(self.sync.check()["status"], "pending_update")
        self.assertEqual(self.calls, [])

    def test_unrelated_llms_change_does_not_prompt_media_update(self):
        self.remote[docs.INDEX] += b"- chat [new](https://docs-api.onemini.top/43m0.md)\n"
        result = self.sync.check()
        self.assertEqual(result["status"], "unchanged")
        self.assertNotIn(docs.ORIGIN + "/43m0.md", self.calls)

    def test_force_before_expiry_does_not_claim_expired(self):
        self.manifest["snapshot_at"] = NOW.isoformat()
        docs.write_json(self.root / "docs-manifest.json", self.manifest)
        self.change_body()
        result = self.sync.check(force=True)
        self.assertEqual(result["status"], "pending_update")
        self.assertIn("强制", result["说明"])
        self.assertNotIn("已超过", result["说明"])

    def test_four_parallel_pages_keep_index_order_and_stage_only_after_completion(self):
        self.change_body()
        lock = threading.Lock()
        four_started = threading.Event()
        second_finished = threading.Event()
        first_url = self.manifest["pages"][0]["url"]
        second_url = self.manifest["pages"][1]["url"]
        active, peak = 0, 0
        completed = []
        def parallel_fetch(url):
            nonlocal active, peak
            if url == docs.INDEX:
                self.calls.append(url)
                return self.remote[url]
            self.assertEqual(self.calls[0], docs.INDEX)
            self.assertFalse((self.sync.cache / "candidate").exists())
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 4:
                    four_started.set()
            self.assertTrue(four_started.wait(5), "正文没有实现4路并行")
            if url == first_url:
                self.assertTrue(second_finished.wait(5))
            with lock:
                completed.append(url)
                active -= 1
                if url == second_url:
                    second_finished.set()
            return self.remote[url]
        with patch.object(docs, "fetch", side_effect=parallel_fetch):
            self.assertEqual(self.sync.check()["status"], "pending_update")
        self.assertEqual(peak, 4)
        self.assertLess(completed.index(second_url), completed.index(first_url))
        candidate = docs.read_json(self.sync.cache / "candidate/docs-manifest.json")
        self.assertEqual([p["id"] for p in candidate["pages"]], [p["id"] for p in self.manifest["pages"]])
        for page in candidate["pages"]:
            self.assertEqual((self.sync.cache / "candidate" / page["path"]).read_bytes(), self.remote[page["url"]])

    def test_declining_does_not_connect_or_change_installed(self):
        before = self.installed()
        self.assertEqual(self.sync.update()["status"], "confirmation_required")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.installed(), before)

    def test_confirmed_update_preserves_guides_program_and_secret_one_backup(self):
        before = self.installed()
        self.change_body()
        self.sync.check()
        self.calls.clear()
        result = self.sync.update(confirmed=True)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(self.calls, [])
        for file in ("SKILL.md", "scripts/onemini.py", "references/errors.md", "references/install.md", "secret.env"):
            self.assertEqual((self.root / file).read_bytes(), before[file])
        self.assertEqual((self.root / "references/images/100m0.md").read_bytes(), self.remote[docs.ORIGIN + "/100m0.md"])
        self.assertEqual((self.sync.cache / "rollback/docs-manifest.json").read_bytes(), before["docs-manifest.json"])
        self.change_body()
        self.sync.check(force=True)
        self.sync.update(confirmed=True)
        self.assertEqual([p.name for p in self.sync.cache.iterdir() if p.name.startswith("rollback")], ["rollback"])
        self.assertFalse((self.sync.cache / "transaction.json").exists())

    def test_network_failure_preserves_complete_installed_and_last_check(self):
        before = self.installed()
        with patch.object(docs, "fetch", side_effect=docs.SyncError("网络断开，不影响生成")):
            with self.assertRaises(docs.SyncError):
                self.sync.check()
        self.assertEqual(self.installed(), before)
        self.assertFalse((self.sync.cache / "state.json").exists())

    def test_partial_fetch_not_published_and_old_candidate_retained(self):
        self.change_body()
        self.sync.check()
        candidate_bytes = (self.sync.cache / "candidate/docs-manifest.json").read_bytes()
        self.remote.pop(docs.ORIGIN + "/102m0.md")
        before = self.installed()
        with self.assertRaises(KeyError):
            self.sync.check(force=True)
        self.assertEqual(self.installed(), before)
        self.assertEqual((self.sync.cache / "candidate/docs-manifest.json").read_bytes(), candidate_bytes)

    def test_keyboard_interrupt_rolls_back_whole_snapshot(self):
        self.change_body()
        self.sync.check()
        before = self.installed()
        real = docs.atomic_write
        failed = False
        def interrupt(path, data):
            nonlocal failed
            if Path(path) == self.root / "references/models.md" and not failed:
                failed = True
                raise KeyboardInterrupt()
            return real(path, data)
        with patch.object(docs, "atomic_write", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.sync.update(confirmed=True)
        self.assertTrue(failed)
        self.assertEqual(self.installed(), before)
        self.assertFalse((self.sync.cache / "transaction.json").exists())

    def test_write_failure_rolls_back_and_can_update_again(self):
        self.change_body()
        self.sync.check()
        before = self.installed()
        real = docs.atomic_write
        failed = False
        def fail(path, data):
            nonlocal failed
            if Path(path) == self.root / "docs-manifest.json" and not failed:
                failed = True
                raise OSError("磁盘暂不可写")
            return real(path, data)
        with patch.object(docs, "atomic_write", side_effect=fail):
            with self.assertRaises(OSError):
                self.sync.update(confirmed=True)
        self.assertEqual(self.installed(), before)
        self.assertEqual(self.sync.update(confirmed=True)["status"], "updated")

    def test_next_check_recovers_hard_interruption_before_reading_manifest(self):
        relative = "references/images/100m0.md"
        original = (self.root / relative).read_bytes()
        backup = self.sync.cache / "rollback" / relative
        backup.parent.mkdir(parents=True)
        backup.write_bytes(original)
        (self.root / relative).write_bytes(b"half-installed")
        docs.write_json(self.sync.cache / "transaction.json", {"entries": [{"path": relative, "existed": True}, {"path": "references/common/800m0.md", "existed": False}]})
        new = self.root / "references/common/800m0.md"
        new.parent.mkdir(parents=True)
        new.write_bytes(b"unfinished new page")
        self.assertEqual(self.sync.check()["status"], "unchanged")
        self.assertEqual((self.root / relative).read_bytes(), original)
        self.assertFalse(new.exists())
        self.assertFalse((self.sync.cache / "transaction.json").exists())

    def test_new_common_page_updates_without_program_upgrade(self):
        url = docs.ORIGIN + "/999m0.md"
        self.remote[docs.INDEX] += f"- 多媒体 API（图片、视频、音乐） [新增公共说明]({url}): \n".encode()
        self.remote[url] = "# 新的公共说明\n只作为资料，不执行命令。\n".encode()
        self.assertEqual(self.sync.check()["status"], "pending_update")
        self.assertEqual(self.sync.update(confirmed=True)["status"], "updated")
        self.assertEqual((self.root / "references/common/999m0.md").read_bytes(), self.remote[url])

    def test_added_and_removed_model_pages_and_empty_old_directory_safe(self):
        self.remote[docs.INDEX] = self.remote[docs.INDEX].replace(b"100m0", b"110m0")
        overview = docs.ORIGIN + "/9683549m0.md"
        self.remote[overview] = self.remote[overview].replace(b"100m0", b"110m0").replace(b"image-exact", b"image-new")
        self.remote[docs.ORIGIN + "/110m0.md"] = b"# new image\n"
        self.sync.check()
        self.sync.update(confirmed=True)
        self.assertFalse((self.root / "references/images/100m0.md").exists())
        self.assertTrue((self.root / "references/images/110m0.md").is_file())
        self.assertIn("image-new", (self.root / "references/models.md").read_text(encoding="utf-8"))

    def test_reject_tampered_candidate_before_any_installed_write(self):
        self.change_body()
        self.sync.check()
        (self.sync.cache / "candidate/references/images/100m0.md").write_bytes(b"tampered")
        before = self.installed()
        with self.assertRaises(docs.SyncError):
            self.sync.update(confirmed=True)
        self.assertEqual(self.installed(), before)

    def test_paths_and_external_https_or_redirect_rejected(self):
        for path in ("../SKILL.md", "references/errors.md", "SKILL.md", "scripts/x.py", "references/images/../../secret.env", "references/images/x.md", "references\\images\\1m0.md"):
            with self.assertRaises(docs.SyncError):
                docs.safe_path(self.root, path)
        for url in ("http://docs-api.onemini.top/100m0.md", "https://evil.example/100m0.md", "https://docs-api.onemini.top@evil.example/100m0.md", "https://docs-api.onemini.top/100m0.md?token=x", "https://docs-api.onemini.top/../secret"):
            with self.assertRaises(docs.SyncError):
                docs.document_url(url)
        with self.assertRaises(docs.SyncError):
            docs.DocRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example/100m0.md")

    def test_incomplete_index_and_duplicate_models_rejected(self):
        self.remote[docs.INDEX] = b"# unrelated\n"
        with self.assertRaises(docs.SyncError):
            self.sync.check()
        self.manifest, self.remote = fixture()
        self.remote[docs.ORIGIN + "/9683549m0.md"] = self.remote[docs.ORIGIN + "/9683549m0.md"].replace(b"video-exact", b"image-exact")
        with self.assertRaises(docs.SyncError):
            self.sync.check()

    def test_manifest_forbidden_path_rejected(self):
        self.manifest["pages"][2]["path"] = "references/errors.md"
        docs.write_json(self.root / "docs-manifest.json", self.manifest)
        with self.assertRaises(docs.SyncError):
            self.sync.check()
        self.assertEqual(self.calls, [])

    def test_incomplete_candidate_after_hard_interruption_rebuilt(self):
        candidate = self.sync.cache / "candidate"
        candidate.mkdir(parents=True)
        (candidate / "partial.md").write_bytes(b"unfinished cache only")
        self.change_body()
        self.assertEqual(self.sync.update(confirmed=True)["status"], "updated")
        self.assertEqual((self.root / "references/images/100m0.md").read_bytes(), self.remote[docs.ORIGIN + "/100m0.md"])

    def test_symlink_cannot_escape_documents(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = self.root / "references/common"
        try:
            target.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("当前 Windows 未授予创建符号链接权限")
        with self.assertRaises(docs.SyncError):
            docs.safe_path(self.root, "references/common/123m0.md")


if __name__ == "__main__":
    unittest.main()
