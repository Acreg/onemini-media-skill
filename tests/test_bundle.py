"""公开技能包完整性及 Windows UTF-8 CLI 捕获验收。"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def test_manifest_pages_hashes_files_and_122_unique_ids(self):
        manifest = json.loads((ROOT / "docs-manifest.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(len(manifest["pages"]), 31)
        ids, page_ids = [], []
        for page in manifest["pages"]:
            file = (ROOT / page["path"]).resolve()
            self.assertTrue(file.is_relative_to((ROOT / "references").resolve()))
            self.assertTrue(file.is_file(), page["path"])
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), page["sha256"], page["path"])
            ids.extend(page["model_ids"])
            page_ids.append(page["id"])
        self.assertEqual(len(ids), 122)
        self.assertEqual(len(set(ids)), 122)
        self.assertEqual(len(set(page_ids)), 31)
        counts = {kind: sum(len(p["model_ids"]) for p in manifest["pages"] if p["kind"] == kind) for kind in ("image", "video", "music")}
        self.assertEqual(counts, {"image": 59, "video": 61, "music": 2})

    def test_model_index_points_to_every_page_and_every_id(self):
        manifest = json.loads((ROOT / "docs-manifest.json").read_text(encoding="utf-8-sig"))
        index = (ROOT / "references/models.md").read_text(encoding="utf-8")
        for page in manifest["pages"]:
            self.assertIn("./" + page["path"].removeprefix("references/"), index)
            for model in page["model_ids"]:
                self.assertIn("`" + model + "`", index)

    def test_cli_stdout_stderr_bytes_strict_utf8_even_gbk_environment(self):
        environment = {**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONDONTWRITEBYTECODE": "1"}
        for script in ("onemini.py", "sync_docs.py"):
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"], capture_output=True, env=environment, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("用法", result.stdout.decode("utf-8", errors="strict"))
            result.stderr.decode("utf-8", errors="strict")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/onemini.py"), "目录", "--离线"], capture_output=True, env=environment, check=False)
        self.assertEqual(result.returncode, 0)
        body = json.loads(result.stdout.decode("utf-8", errors="strict"))
        self.assertEqual(len(body["data"]), 122)


if __name__ == "__main__":
    unittest.main()
