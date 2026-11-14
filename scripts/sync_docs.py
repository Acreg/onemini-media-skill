#!/usr/bin/env python3
"""只从正式同域 HTTPS 文档检查、缓存并经确认更新离线资料。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "https://docs-api.onemini.top"
INDEX = ORIGIN + "/llms.txt"
GROUPS = {"image": "images", "video": "videos", "music": "music"}
COMMON = {"9683548m0": "references/tasks.md", "9683549m0": "references/quickstart.md"}


class SyncError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_path(root, relative):
    """只允许指定资料文件，拒绝越界、软链接及 Windows 特殊路径。"""
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise SyncError("文档路径非法。")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or relative != rel.as_posix():
        raise SyncError("文档路径超出允许范围。")
    allowed = relative in {"docs-manifest.json", "references/models.md", *COMMON.values()}
    allowed = allowed or bool(re.fullmatch(r"references/(?:images|videos|music|common)/[0-9]+m[0-9]+\.md", relative))
    if not allowed:
        raise SyncError("更新只允许模型说明、公共说明、型号索引及 manifest，不能修改其他文件。")
    root = Path(root).resolve()
    target = root / rel
    for current in [target, *target.parents]:
        if current == root:
            break
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise SyncError("文档路径含链接，已阻止越界写入。")
    if not target.resolve().is_relative_to(root):
        raise SyncError("文档路径越界。")
    return target


def document_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.netloc != "docs-api.onemini.top" or p.query or p.fragment:
        raise SyncError("只接受正式文档同域 HTTPS 地址，不接受外域、认证信息或查询参数。")
    if p.path != "/llms.txt" and not re.fullmatch(r"/[0-9]+m[0-9]+\.md", p.path):
        raise SyncError("文档链接不是允许的索引或 Markdown 页面。")
    return url


class DocRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        document_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    document_url(url)
    try:
        opener = urllib.request.build_opener(DocRedirect())
        with opener.open(urllib.request.Request(url, headers={"Accept": "text/plain, text/markdown"}), timeout=30) as response:
            data = response.read()
        text = data.decode("utf-8-sig")
        if not text.strip() or text.lstrip().lower().startswith(("<!doctype html", "<html")):
            raise SyncError("文档服务未返回有效 Markdown/文本资料。")
        return data
    except (urllib.error.URLError, OSError, UnicodeError, HTTPException) as exc:
        raise SyncError("文档检查网络失败或正文编码异常；不影响使用现有说明生成作品。" + str(exc)) from None


def parse_index(data):
    pages, seen = [], set()
    in_media = False
    for line in data.decode("utf-8-sig").splitlines():
        if line.startswith("#"):
            in_media = "多媒体" in line
        if "多媒体" not in line and not in_media:
            continue
        match = re.search(r"\[([^\]]+)\]\((https://[^)]+)\)", line)
        if not match:
            continue
        title, url = match.groups()
        document_url(url)
        page_id = Path(urllib.parse.urlsplit(url).path).stem
        if page_id in seen:
            raise SyncError("正式索引中出现重复文档编号。")
        kind = next((k for word, k in (("图片模型", "image"), ("视频模型", "video"), ("音乐模型", "music")) if word in line), "common")
        if kind == "common":
            path = COMMON.get(page_id, f"references/common/{page_id}.md")
        else:
            path = f"references/{GROUPS[kind]}/{page_id}.md"
        pages.append({"id": page_id, "title": title, "kind": kind, "url": url, "path": path, "model_ids": []})
        seen.add(page_id)
    if not set(COMMON).issubset(seen) or not all(any(p["kind"] == kind for p in pages) for kind in GROUPS):
        raise SyncError("索引缺少公共说明或某类多媒体模型，未建立不完整候选。")
    return pages


def extract_models(quickstart, pages):
    by_id = {p["id"]: p for p in pages}
    for line in quickstart.decode("utf-8-sig").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        match = re.search(r"\]\(https://docs-api\.onemini\.top/([0-9]+m[0-9]+)(?:\.md)?\)", line)
        if not match or match.group(1) not in by_id:
            continue
        ids = re.findall(r"`([^`]+)`", line[match.end():])
        if ids:
            by_id[match.group(1)]["model_ids"].extend(ids)
    all_ids = []
    for page in pages:
        if page["kind"] != "common" and not page["model_ids"]:
            raise SyncError("总览未列出某页的精确型号；未建立可能缺失型号的候选。")
        all_ids.extend(page["model_ids"])
    if len(all_ids) != len(set(all_ids)):
        raise SyncError("总览包含重复精确型号，未更新本地说明。")


def validate_manifest(manifest, root):
    if manifest.get("schema_version") != 1 or manifest.get("source_index") != INDEX:
        raise SyncError("不支持的 manifest 格式或文档来源。")
    seen, models = set(), set()
    for page in manifest["pages"]:
        page_id = page["id"]
        if not re.fullmatch(r"[0-9]+m[0-9]+", page_id) or page_id in seen:
            raise SyncError("文档编号非法或重复。")
        kind = page["kind"]
        if kind not in {"common", *GROUPS}:
            raise SyncError("文档类型非法。")
        expected = COMMON.get(page_id, f"references/common/{page_id}.md") if kind == "common" else f"references/{GROUPS[kind]}/{page_id}.md"
        if page["path"] != expected or page["url"] != f"{ORIGIN}/{page_id}.md":
            raise SyncError("文档来源与本地路径不符合约定。")
        document_url(page["url"])
        safe_path(root, page["path"])
        if not re.fullmatch(r"[a-f0-9]{64}", page["sha256"]):
            raise SyncError("正文摘要格式非法。")
        for model in page["model_ids"]:
            if not isinstance(model, str) or not model or model in models:
                raise SyncError("精确型号为空或重复。")
            models.add(model)
        seen.add(page_id)


def models_markdown(manifest):
    lines = ["# Onemini 多媒体模型离线检索", "", "文档快照：" + manifest["snapshot_at"], "", "使用完整精确型号；目录收录不代表当前密钥权限。先按型号查阅下列参数页。", "", "公共说明：[快速开始](./quickstart.md) · [任务查询、下载与停止](./tasks.md)", ""]
    for kind, title in (("image", "图片模型"), ("video", "视频模型"), ("music", "音乐模型")):
        lines.extend(["## " + title, "", "| 模型文档 | 精确模型 ID |", "|---|---|"])
        for page in manifest["pages"]:
            if page["kind"] == kind:
                title_safe = page["title"].replace("|", "／").replace("[", "（").replace("]", "）")
                relative = page["path"].removeprefix("references/")
                lines.append(f"| [{title_safe}](./{relative}) | " + "、".join("`" + m.replace("|", "&#124;") + "`" for m in page["model_ids"]) + " |")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


class Syncer:
    def __init__(self, root=ROOT, now=None):
        self.root = Path(root).resolve()
        self.cache = self.root / ".cache" / "docs"
        # 缓存目录也必须留在技能根目录，不能经链接跳出。
        if not self.cache.resolve().is_relative_to(self.root):
            raise SyncError("文档缓存目录越界。")
        self.now = now or datetime.now(timezone.utc)

    def remove_cache(self, path):
        path = Path(path)
        if path.is_symlink() or not path.resolve().is_relative_to(self.cache.resolve()) or path.resolve() == self.cache.resolve():
            raise SyncError("拒绝清理文档缓存以外的路径。")
        if path.exists():
            shutil.rmtree(path)

    def manifest(self):
        manifest = read_json(safe_path(self.root, "docs-manifest.json"))
        validate_manifest(manifest, self.root)
        return manifest

    def state(self):
        path = self.cache / "state.json"
        return read_json(path) if path.exists() else {}

    def check(self, force=False):
        self.recover()
        manifest = self.manifest()
        state = self.state()
        base_time = state.get("last_checked_at", manifest["snapshot_at"])
        next_check = timestamp(base_time) + timedelta(days=manifest.get("check_interval_days", 7))
        candidate = self.cache / "candidate"
        if not force and self.now < next_check:
            pending = (candidate / "candidate-meta.json").is_file()
            return {"说明": "检测到线上说明有更新，内置说明可能影响使用体验。是否更新？" if pending else "未到7天检查周期，未联网；内置说明可继续使用。", "status": "pending_update" if pending else "not_due", "next_check_at": next_check.isoformat(), "changes": state.get("changes", []) if pending else []}
        # 正文每次到期都抓取，不能用索引摘要未变跳过正文检查。
        index_bytes = fetch(INDEX)
        pages = parse_index(index_bytes)
        # 索引先获取；正文最多4路并行，map 保持索引顺序，全部成功后才构建候选。
        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched = list(pool.map(fetch, (page["url"] for page in pages)))
        bodies = {}
        for page, body in zip(pages, fetched):
            bodies[page["id"]] = body
            page["sha256"] = digest(body)
        extract_models(bodies["9683549m0"], pages)
        installed = {p["id"]: p for p in manifest["pages"]}
        changes = []
        for page in pages:
            old = installed.get(page["id"])
            if not old:
                changes.append({"id": page["id"], "title": page["title"], "change": "新增"})
            elif any(old.get(key) != page.get(key) for key in ("sha256", "title", "kind", "path", "model_ids")) or not safe_path(self.root, page["path"]).is_file() or digest(safe_path(self.root, page["path"]).read_bytes()) != page["sha256"]:
                changes.append({"id": page["id"], "title": page["title"], "change": "正文或型号变化"})
        page_ids = {p["id"] for p in pages}
        changes += [{"id": p["id"], "title": p["title"], "change": "移除"} for p in manifest["pages"] if p["id"] not in page_ids]
        state = {"last_checked_at": self.now.isoformat(), "source_index_sha256": digest(index_bytes), "changes": changes}
        if changes:
            self.cache.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix="staging-", dir=self.cache))
            try:
                updated = {"schema_version": 1, "snapshot_at": self.now.isoformat(), "source_index": INDEX, "source_index_sha256": digest(index_bytes), "check_interval_days": manifest.get("check_interval_days", 7), "pages": pages}
                validate_manifest(updated, self.root)
                for page in pages:
                    target = safe_path(stage, page["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bodies[page["id"]])
                safe_path(stage, "references/models.md").write_bytes(models_markdown(updated))
                write_json(safe_path(stage, "docs-manifest.json"), updated)
                write_json(stage / "candidate-meta.json", {"installed_sha256": digest((self.root / "docs-manifest.json").read_bytes())})
                self.remove_cache(candidate)
                # Windows 对刚写完的目录重命名可能返回 WinError 5；逐文件复制完整候选，完成标记最后写入。
                try:
                    shutil.copytree(stage, candidate, ignore=shutil.ignore_patterns("candidate-meta.json"))
                    atomic_write(candidate / "candidate-meta.json", (stage / "candidate-meta.json").read_bytes())
                except BaseException:
                    self.remove_cache(candidate)
                    raise
            finally:
                if stage.exists():
                    self.remove_cache(stage)
        else:
            self.remove_cache(candidate)
        write_json(self.cache / "state.json", state)
        return {"说明": ("内置说明已超过7天检查周期；" if self.now >= next_check else "已强制检查；") + "检测到线上多媒体开发文档有新内容，不更新可能影响使用体验。是否更新本地说明？暂不更新仍可生成。" if changes else "索引和全部参数正文均无变化；无需更新。", "status": "pending_update" if changes else "unchanged", "changes": changes, "checked_at": self.now.isoformat()}

    def recover(self):
        journal = self.cache / "transaction.json"
        if not journal.exists():
            return False
        entries = read_json(journal)["entries"]
        rollback = self.cache / "rollback"
        # 先验证整个恢复集，再写入；不能让损坏的日志把范围扩大。
        for entry in entries:
            safe_path(self.root, entry["path"])
            backup = safe_path(rollback, entry["path"])
            if entry["existed"] and not backup.is_file():
                raise SyncError("回退资料不完整，保留事务日志以便核对；未继续覆盖资料。")
        for entry in entries:
            target = safe_path(self.root, entry["path"])
            if entry["existed"]:
                atomic_write(target, safe_path(rollback, entry["path"]).read_bytes())
            else:
                target.unlink(missing_ok=True)
        journal.unlink()
        return True

    def update(self, confirmed=False):
        if not confirmed:
            return {"说明": "尚未确认，未联网或覆盖文档。请先询问用户是否更新，再使用 更新 --确认。", "status": "confirmation_required"}
        self.recover()
        candidate = self.cache / "candidate"
        current = self.manifest()
        current_sha = digest((self.root / "docs-manifest.json").read_bytes())
        if not (candidate / "candidate-meta.json").is_file() or read_json(candidate / "candidate-meta.json").get("installed_sha256") != current_sha:
            check = self.check(force=True)
            if check["status"] == "unchanged":
                return check
        proposed = read_json(candidate / "docs-manifest.json")
        validate_manifest(proposed, self.root)
        for page in proposed["pages"]:
            if digest(safe_path(candidate, page["path"]).read_bytes()) != page["sha256"]:
                raise SyncError("候选正文摘要不匹配，未覆盖已安装说明；请强制重新检查。")
        expected_models = models_markdown(proposed)
        if safe_path(candidate, "references/models.md").read_bytes() != expected_models:
            raise SyncError("候选型号索引不完整，未更新资料。")
        new_paths = {p["path"] for p in proposed["pages"]} | {"references/models.md", "docs-manifest.json"}
        all_paths = sorted(new_paths | {p["path"] for p in current["pages"]})
        rollback = self.cache / "rollback"
        self.remove_cache(rollback)
        rollback.mkdir(parents=True)
        entries = []
        for relative in all_paths:
            target = safe_path(self.root, relative)
            entries.append({"path": relative, "existed": target.exists()})
            if target.exists():
                backup = safe_path(rollback, relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(target.read_bytes())
        write_json(self.cache / "transaction.json", {"entries": entries})
        try:
            # manifest 最后替换；任一写入失败或进程中断都恢复整个旧快照。
            for relative in [p for p in all_paths if p != "docs-manifest.json"] + ["docs-manifest.json"]:
                target = safe_path(self.root, relative)
                if relative in new_paths:
                    atomic_write(target, safe_path(candidate, relative).read_bytes())
                else:
                    target.unlink(missing_ok=True)
        except BaseException:
            self.recover()
            raise
        (self.cache / "transaction.json").unlink()
        self.remove_cache(candidate)
        write_json(self.cache / "state.json", {"last_checked_at": self.now.isoformat(), "changes": []})
        return {"说明": "离线文档已更新，保留一份回退；未修改脚本、SKILL、密钥或本地错误指南。", "status": "updated", "pages": len(proposed["pages"]), "snapshot_at": proposed["snapshot_at"]}


def main(argv=None):
    from onemini import ChineseParser, configure_streams
    configure_streams()
    p = ChineseParser(description="匿名检查与确认更新正式多媒体说明，不影响正常生成。")
    sub = p.add_subparsers(dest="command", required=True, title="命令")
    c = sub.add_parser("检查", help="7天到期后检查索引及全部正文")
    c.add_argument("--强制", action="store_true", help="忽略检查周期，仅检查并缓存候选")
    c = sub.add_parser("更新", help="用户同意后替换完整候选资料")
    c.add_argument("--确认", action="store_true", help="已取得用户更新同意")
    try:
        args = p.parse_args(argv)
        syncer = Syncer()
        result = syncer.check(args.强制) if args.command == "检查" else syncer.update(args.确认)
        ok = result["status"] != "confirmation_required"
        print(json.dumps({"ok": ok, **result}, ensure_ascii=False))
        return 0 if ok else 2
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "说明": "文档操作已中断；已安装资料保留或回退，下次确认更新会先恢复未完成事务。不影响正常生成。"}, ensure_ascii=False))
        return 130
    except Exception as exc:
        print(json.dumps({"ok": False, "说明": "文档操作失败，未要求停止生成；可继续使用现有说明。", "diagnostic": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
