#!/usr/bin/env python3
"""Onemini 图片、视频、音乐真实客户端（Python 3.10+，仅标准库）。"""
import argparse
import base64
import binascii
import json
import hashlib
from http.client import HTTPException
import mimetypes
import os
from pathlib import Path
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
ENV_NAME = "【务必安全使用不要打印出来】.env"
LINES = {"主站": "https://api.onemini.top", "香港": "https://api-hk.onemini.top", "亚太": "https://api-ap.onemini.top"}
ROUTES = {"图片": "/v1/images/generations", "视频": "/v1/videos/generations", "音乐": "/v1/music/generations"}
OPERATIONS = {"图片": "image", "视频": "video", "音乐": "music"}
POLL_INTERVAL = 3
SECRETS = set()


class ClientError(Exception):
    def __init__(self, message, *, code=1, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def sanitize(value):
    if isinstance(value, dict):
        return {k: ("[已隐藏]" if k.lower().replace("-", "_") in {"authorization", "api_key", "onemini_api_key", "access_token"} else sanitize(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        for secret in sorted(SECRETS, key=len, reverse=True):
            for variant in {secret, urllib.parse.quote(secret, safe=""), urllib.parse.quote_plus(secret)}:
                value = value.replace(variant, "[已隐藏]")
        return re.sub(r"(?i)Bearer\s+[^\s\"'<>]+", "Bearer [已隐藏]", value)
    return value


def json_read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ClientError("无法读取 JSON 文件，请检查路径及 JSON 格式。", path=str(path), diagnostic=str(exc)) from None


def json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(sanitize(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def init_config(root=ROOT):
    path = Path(root) / ENV_NAME
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write("# 只在本地填写；不要打印、上传或提交此文件。\nONEMINI_API_KEY=\nONEMINI_BASE_URL=https://api.onemini.top\n")
        path.chmod(0o600)
        return {"说明": "已创建本地空配置，请在文件中填写密钥；未联网。", "配置文件": str(path)}
    except FileExistsError:
        return {"说明": "配置已存在，未覆盖任何内容。", "配置文件": str(path)}


def load_config(root=ROOT, line=None):
    values = {}
    path = Path(root) / ENV_NAME
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#") and "=" in raw:
                k, v = raw.removeprefix("export ").split("=", 1)
                values[k.strip()] = v.strip().strip("\"'")
    key = os.environ.get("ONEMINI_API_KEY", values.get("ONEMINI_API_KEY", "")).strip()
    if key:
        SECRETS.add(key)
    base = LINES[line] if line else os.environ.get("ONEMINI_BASE_URL", values.get("ONEMINI_BASE_URL", LINES["主站"]))
    base = base.rstrip("/")
    if base not in LINES.values():
        raise ClientError("认证地址仅支持主站、香港、亚太三条 HTTPS 线路；请检查 ONEMINI_BASE_URL（不含 /v1）。")
    if not key or "\r" in key or "\n" in key:
        raise ClientError("尚未配置有效密钥。请先初始化并在本地安全配置文件中填写 ONEMINI_API_KEY。")
    return key, base


class NoAPIRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("API 返回重定向；已停止，未转发密钥或重放生成请求。", http_status=code)


def validate_download_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ClientError("产物地址必须是无内嵌凭据的 HTTP/HTTPS URL。")
    decoded = urllib.parse.unquote_plus(url)
    if any(secret in decoded for secret in SECRETS):
        raise ClientError("产物地址包含 API 密钥，已阻止向第三方泄漏。")
    return url


class DownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_download_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            redirected.remove_header("Authorization")
            redirected.remove_header("Cookie")
        return redirected


HTTP_HINTS = {
    400: "参数不符合接口要求，请核对精确型号、字段类型、素材及条件必填项。",
    401: "密钥无效或未提供，请核对本地配置与所属环境。",
    402: "可能存在余额或额度限制，请读取业务错误并核对账户。",
    403: "当前密钥可能无模型、分组或任务权限，请核对所属线路与权限。",
    404: "接口或任务不存在，请确认本站任务号、原线路及密钥归属。",
    409: "请求身份冲突或状态冲突，请核对原请求，不要更换身份重复生成。",
    413: "素材过大，请按文档限制压缩后上传。",
    422: "参数校验未通过，请核对模型专属字段及互斥条件。",
    429: "请求频率或并发受限；请稍后查询原任务，生成不会自动重试。",
}


def error_fields(payload):
    if not isinstance(payload, dict):
        return None, sanitize(str(payload))
    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("code", payload.get("code")), sanitize(error.get("message", payload.get("message")))
    return payload.get("code"), sanitize(error if isinstance(error, str) else payload.get("message"))


class Client:
    def __init__(self, root=ROOT, line=None, timeout=60):
        self.root = Path(root)
        self.key, self.base = load_config(self.root, line)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoAPIRedirect())

    def request(self, method, path, body=None, *, headers=None, query=None, content_type="application/json"):
        if self.base not in LINES.values() or not path.startswith("/v1/") or "://" in path or "\r" in path or "\n" in path:
            raise ClientError("API 地址不在允许的认证线路或路径内。")
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = body if isinstance(body, bytes) else (json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
        hdr = {"Authorization": "Bearer " + self.key, "Accept": "application/json"}
        if data is not None:
            hdr["Content-Type"] = content_type
        hdr.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdr, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                status = response.status
                response_headers = dict(response.headers)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read(65536).decode("utf-8", errors="replace")
            try:
                diagnostic = json.loads(raw)
            except ValueError:
                diagnostic = raw
            error_code, upstream_message = error_fields(diagnostic)
            hint = "未提供 API 密钥，请在本地安全配置中填写 ONEMINI_API_KEY；这不表示密钥无效。" if error_code == "API_KEY_REQUIRED" else HTTP_HINTS.get(exc.code, "服务端或上游返回错误；请保留原任务标识核对，不要盲目重新生成。")
            raise ClientError(hint, http_status=exc.code, error_code=error_code, upstream_message=upstream_message,
                              diagnostic=sanitize(diagnostic), task_id=exc.headers.get("X-Media-Task-ID"), retry_after=exc.headers.get("Retry-After")) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, HTTPException) as exc:
            raise ClientError("网络失败或请求超时；生成是否已提交尚不能确定，请通过原记录恢复查询。", network_error=True, diagnostic=sanitize(str(exc))) from None
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ClientError("接口未返回有效 JSON；请保留原身份核对任务，不要重复提交。", http_status=status, response_invalid=True,
                              task_id=response_headers.get("X-Media-Task-ID")) from None
        if isinstance(result, dict) and (result.get("error") or result.get("success") is False or (result.get("code") not in (None, 0, 200, "OK", "success") and result.get("message"))):
            raise ClientError("接口报告业务失败；请查看已脱敏错误信息并核对原任务。", http_status=status, error_code=error_fields(result)[0], upstream_message=error_fields(result)[1], diagnostic=sanitize(result))
        return result, response_headers

    def task(self, task_id):
        return self.request("GET", "/v1/media/tasks/" + urllib.parse.quote(str(task_id), safe=""))[0]

    def tasks(self, **query):
        return self.request("GET", "/v1/media/tasks", query=query)[0]


def task_object(payload):
    obj = payload.get("data", payload) if isinstance(payload, dict) else None
    if isinstance(obj, dict) and (obj.get("task_id") or obj.get("object") == "media.task" or (obj.get("id") and obj.get("status"))):
        return obj
    return None


def assets_from(payload):
    """保留多产物，过滤重复标准化链接；不把任务 id 当成 URL。"""
    task = task_object(payload)
    if task is not None:
        payload = task.get("result", {})
    assets, seen = [], set()
    url_fields = {"url", "urls", "audio_url", "video_url", "image_url", "download_url", "cover_url", "image_large_url", "image_small_url"}

    def add(value, kind):
        if isinstance(value, list):
            for item in value:
                add(item, kind)
        elif isinstance(value, str) and (value.startswith(("https://", "http://", "data:")) or kind == "base64"):
            identity = (kind if kind == "base64" else "url", value)
            if identity not in seen:
                seen.add(identity)
                assets.append({"kind": kind, "value": value})

    def visit(node, context=""):
        if isinstance(node, list):
            for item in node:
                if isinstance(item, str):
                    add(item, context)
                else:
                    visit(item, context)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in {"lyrics", "lyric", "lyrics_text"} and value:
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
                    identity = ("lyrics", text)
                    if identity not in seen:
                        seen.add(identity)
                        assets.append({"kind": "lyrics", "value": text})
                elif key in {"b64_json", "base64"}:
                    add(value, "base64")
                elif key in url_fields:
                    if isinstance(value, dict):
                        visit(value, context)
                    else:
                        add(value, context)
                elif key not in {"error", "input", "input_assets", "metadata", "parameters", "request", "source_reference"}:
                    visit(value, key)
        elif isinstance(node, str) and context in {"result", "data", "images", "videos", "audios", "music"}:
            add(node, context)
    visit(payload)
    return assets


def sniff_mime(chunk, declared=None):
    if chunk.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if chunk.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if chunk.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if chunk.startswith(b"RIFF") and chunk[8:12] == b"WEBP":
        return "image/webp"
    if chunk.startswith(b"RIFF") and chunk[8:12] == b"WAVE":
        return "audio/wav"
    if chunk.startswith(b"ID3") or (len(chunk) > 1 and chunk[0] == 255 and chunk[1] & 224 == 224):
        return "audio/mpeg"
    if chunk.startswith(b"fLaC"):
        return "audio/flac"
    if chunk.startswith(b"OggS"):
        return "audio/ogg"
    if chunk[4:8] == b"ftyp":
        # ftyp 只说明 MP4 容器；M4A 或明确声明的 MP4 音频不能当成视频。
        return "audio/mp4" if chunk[8:12] == b"M4A " or declared == "audio/mp4" else "video/mp4"
    if b"<svg" in chunk.lstrip()[:1024]:
        return "image/svg+xml"
    return None


MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg", "video/mp4": ".mp4", "video/webm": ".webm", "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "text/plain": ".txt"}


def download_assets(payload, output_dir, completed=None):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    completed = list(completed or [])
    already = {item["source"] for item in completed if Path(item["path"]).is_file()}
    opener = urllib.request.build_opener(DownloadRedirect())
    assets = assets_from(payload)
    if not assets:
        raise ClientError("任务没有可下载的 URL 或 Base64 产物，请检查实际 result；不会把空结果视为成功。")
    for asset in assets:
        value = asset["value"]
        source = value if asset["kind"] not in {"base64", "lyrics"} and not value.startswith("data:") else asset["kind"] + ":" + hashlib.sha256(value.encode()).hexdigest()
        if source in already:
            continue
        temp = output / (uuid.uuid4().hex + ".part")
        try:
            if asset["kind"] == "lyrics":
                content = value.encode("utf-8")
                temp.write_bytes(content)
                size, mime = len(content), "text/plain"
            elif asset["kind"] == "base64" or value.startswith("data:"):
                mime = ""
                if value.startswith("data:"):
                    header, value = value.split(",", 1)
                    if not header.endswith(";base64"):
                        raise ClientError("不支持非 Base64 的 data URL 产物。")
                    mime = header[5:].split(";", 1)[0]
                content = base64.b64decode(value, validate=True)
                if not content:
                    raise ClientError("返回的 Base64 产物为空。")
                mime = sniff_mime(content[:4096], mime) or mime or "application/octet-stream"
                temp.write_bytes(content)
                size = len(content)
            else:
                validate_download_url(value)
                with opener.open(urllib.request.Request(value, headers={"Accept": "*/*"}), timeout=60) as response:
                    first = response.read(65536)
                    if not first:
                        raise ClientError("产物下载响应为空。")
                    mime = sniff_mime(first, response.headers.get_content_type()) or response.headers.get_content_type()
                    if mime in {"text/html", "application/json"}:
                        raise ClientError("产物地址返回网页或 JSON 而非作品，链接可能已过期；请查询原任务重新获取地址。")
                    size = len(first)
                    with temp.open("wb") as handle:
                        handle.write(first)
                        while True:
                            chunk = response.read(65536)
                            if not chunk:
                                break
                            handle.write(chunk)
                            size += len(chunk)
                    expected = response.headers.get("Content-Length")
                    if expected is not None and size != int(expected):
                        raise ClientError("产物下载不完整，请通过原任务恢复下载。")
            extension = MIME_EXT.get(mime) or mimetypes.guess_extension(mime) or ".bin"
            target = output / (uuid.uuid4().hex + extension)
            os.replace(temp, target)
            completed.append({"source": source, "path": str(target), "mime_type": mime, "bytes": size})
            already.add(source)
        except (urllib.error.URLError, OSError, ValueError, binascii.Error, HTTPException) as exc:
            raise ClientError("作品下载失败；可继续原任务下载，不会重新生成。", diagnostic=sanitize(str(exc)), files=completed) from None
        except ClientError as exc:
            exc.details["files"] = completed
            raise
        finally:
            temp.unlink(missing_ok=True)
    if not any(asset["kind"] != "lyrics" for asset in assets):
        raise ClientError("仅保存了歌词附带文件，未返回 URL/Base64 媒体产物；尚未交付媒体作品。", files=completed)
    return completed


def save_record(client, record, path):
    json_write(path, record)


def process_result(client, record, record_path, payload, wait, *, download=True, require_done=False):
    deadline = time.monotonic() + max(wait, 0)
    while True:
        record["response"] = payload
        task = task_object(payload)
        if task:
            record["task_id"] = task.get("task_id") or task.get("id") or record.get("task_id")
            record["status"] = task.get("status", "submit_unknown")
        else:
            record["status"] = "succeeded" if assets_from(payload) else "submit_unknown"
        save_record(client, record, record_path)
        status = record["status"]
        if status == "succeeded":
            if download:
                try:
                    record["files"] = download_assets(payload, record["output_dir"], record.get("files"))
                except ClientError as exc:
                    record["files"] = exc.details.get("files", record.get("files", []))
                    save_record(client, record, record_path)
                    exc.details.update(record=str(record_path), task_id=record.get("task_id"))
                    raise
                save_record(client, record, record_path)
            return {"说明": "生成完成，已保存作品。" if download else "任务已完成。", "record": str(record_path), **record}
        if status in {"failed", "cancelled"}:
            raise ClientError("原任务失败或已停止；请查看真实错误。", record=str(record_path), task_id=record.get("task_id"), status=status, diagnostic=sanitize(task.get("error") if task else payload))
        if status == "submit_unknown":
            raise ClientError("提交状态待核对，请使用继续命令找回原任务；不会重提生成。", code=3, record=str(record_path), client_ref=record.get("client_ref"), task_id=record.get("task_id"))
        if not record.get("task_id"):
            raise ClientError("响应没有本站任务编号，无法继续轮询，请按业务标记找回。", code=3, record=str(record_path), client_ref=record.get("client_ref"))
        if time.monotonic() >= deadline:
            if wait > 0 or require_done:
                raise ClientError("本地等待结束，服务端任务没有被取消；请使用继续命令恢复查询。", code=3, record=str(record_path), task_id=record["task_id"], status=status)
            return {"说明": "任务尚在执行，未取消也未重新提交。", "record": str(record_path), **record}
        time.sleep(min(POLL_INTERVAL, max(0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            continue
        try:
            payload = client.task(record["task_id"])
        except ClientError as exc:
            exc.details.update(record=str(record_path), task_id=record["task_id"])
            raise


def generate(client, kind, parameters, output_dir=None, wait=600, no_wait=False, request_id=None):
    if not isinstance(parameters, dict) or not isinstance(parameters.get("model"), str) or not parameters["model"]:
        raise ClientError("参数文件必须是包含精确 model 的 JSON 对象。")
    parameters = dict(parameters)
    ref = parameters.setdefault("client_ref", "onemini-" + uuid.uuid4().hex)
    if not isinstance(ref, str) or not ref:
        raise ClientError("client_ref 必须是非空字符串，供未知提交找回原单。")
    identity = request_id or uuid.uuid4().hex
    local_id = uuid.uuid4().hex
    record_path = client.root / ".cache" / "tasks" / (local_id + ".json")
    record = {"base_url": client.base, "request_path": ROUTES[kind], "model": parameters["model"], "operation": OPERATIONS[kind],
              "idempotency_key": identity, "client_ref": ref, "status": "submit_unknown", "files": [],
              "output_dir": str(Path(output_dir or Path.cwd() / "outputs" / local_id).resolve())}
    # 在第一次网络操作前落盘；中断后只能查询，不能自动重发收费 POST。
    save_record(client, record, record_path)
    try:
        payload, headers = client.request("POST", ROUTES[kind], parameters, headers={"Idempotency-Key": identity})
    except ClientError as exc:
        status = exc.details.get("http_status")
        if status and 400 <= status < 500 and status != 408:
            record["status"] = "failed"
        record["error"] = sanitize({"说明": str(exc), **exc.details})
        if exc.details.get("task_id"):
            record["task_id"] = exc.details["task_id"]
        save_record(client, record, record_path)
        exc.details.update(record=str(record_path), client_ref=ref, idempotency_key=identity, status=record["status"])
        raise
    task_id = next((v for k, v in headers.items() if k.lower() == "x-media-task-id"), None)
    if task_id:
        record["task_id"] = task_id
        if task_object(payload) is None and not assets_from(payload):
            payload = {"data": {"task_id": task_id, "status": "queued"}}
    return process_result(client, record, record_path, payload, 0 if no_wait else wait, download=not no_wait or task_object(payload) is None)


def query_task(client, task_id, output_dir=None, wait=0, require_done=False):
    record_path = client.root / ".cache" / "tasks" / (uuid.uuid4().hex + ".json")
    record = {"base_url": client.base, "task_id": task_id, "status": "queued", "files": [],
              "output_dir": str(Path(output_dir or Path.cwd() / "outputs" / uuid.uuid4().hex).resolve())}
    save_record(client, record, record_path)
    try:
        payload = client.task(task_id)
    except ClientError as exc:
        exc.details.update(record=str(record_path), task_id=task_id)
        raise
    return process_result(client, record, record_path, payload, wait, require_done=require_done)


def resume(client, record_path, wait=600):
    record_path = Path(record_path).resolve()
    record = json_read(record_path)
    base = record.get("base_url")
    if base not in LINES.values():
        raise ClientError("原记录的线路不是允许的认证地址，无法恢复。")
    # 只查询原线路，不能被当前配置或 --线路 隐式切到另一环境。
    client.base = base
    if record.get("task_id"):
        payload = client.task(record["task_id"])
    elif record.get("status") == "succeeded" and record.get("response"):
        payload = record["response"]
    elif record.get("client_ref"):
        payload = client.tasks(client_ref=record["client_ref"], limit=100)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list) or len(rows) != 1:
            raise ClientError("原业务标记未找到唯一任务；请用任务命令核对，未重新提交。", code=3, record=str(record_path), client_ref=record["client_ref"], matches=len(rows) if isinstance(rows, list) else 0)
        payload = {"data": rows[0]}
    else:
        raise ClientError("记录中没有本站任务号或业务标记；无法找回，未重新提交。", code=3)
    return process_result(client, record, record_path, payload, wait)


def upload(client, filename, kind, model, days=3):
    path = Path(filename)
    maximum = (30 if kind == "图片" else 50) * 1024 * 1024
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise ClientError("上传文件必须非空，图片不超过30 MiB、视频不超过50 MiB。")
    boundary = "onemini-" + uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # 使用生成的文件名，避免本地文件名注入 multipart 头。
    name = "upload" + re.sub(r"[^.a-zA-Z0-9]", "", path.suffix)
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return client.request("POST", "/v1/media/uploads/" + ("images" if kind == "图片" else "videos"), body,
                          query={"model": model, "retention_days": days}, content_type="multipart/form-data; boundary=" + boundary)[0]


class ChineseParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self.add_argument("-h", "--help", action="help", help="显示帮助并退出")
        self._positionals.title = "位置参数"
        self._optionals.title = "可选参数"

    def format_usage(self):
        return super().format_usage().replace("usage:", "用法：")

    def format_help(self):
        return super().format_help().replace("usage:", "用法：")

    def error(self, message):
        # argparse 的内部报错按字段保留，不回显用户完整命令行。
        translations = {"the following arguments are required:": "缺少必填参数：", "invalid choice:": "不支持的选项：", "unrecognized arguments:": "存在无法识别的参数：", "expected one argument": "需要一个参数值", "invalid float value:": "需要数字秒数：", "invalid int value:": "需要整数：", "argument": "参数", "choose from": "可选"}
        for source, target in translations.items():
            message = message.replace(source, target)
        raise ClientError("命令参数错误。" + message, code=2)


def parser():
    p = ChineseParser(description="调用 Onemini 图片、视频、音乐 API；密钥仅从本地配置或环境读取。")
    p.add_argument("--线路", choices=list(LINES), help="选择 API 线路，默认读取配置或主站")
    sub = p.add_subparsers(dest="command", required=True, title="命令")
    def command(name, help):
        cp = sub.add_parser(name, help=help, description=help)
        cp.add_argument("--线路", choices=list(LINES), default=argparse.SUPPRESS, help="选择 API 线路")
        return cp
    command("初始化", "创建安全配置，不覆盖已有配置，不联网")
    c = command("目录", "查询当前密钥真实目录或内置离线目录")
    c.add_argument("--离线", action="store_true", help="使用内置型号，不需要密钥或网络")
    for name in ("生成", "估价"):
        c = command(name, "提交真实生成请求并保存恢复记录" if name == "生成" else "可选估价，不发起生成")
        c.add_argument("--类型", choices=list(ROUTES), required=True, help="媒体类型")
        c.add_argument("--参数文件", required=True, help="原生参数 JSON 文件路径")
        if name == "生成":
            c.add_argument("--输出目录", help="作品保存目录")
            c.add_argument("--等待秒数", type=float, default=600, help="本地最多等待秒数，默认600")
            c.add_argument("--不等待", action="store_true", help="受理后返回；同步结果仍下载")
            c.add_argument("--请求标识", help="可选准备接口返回的 idempotency_key")
    for name in ("查询", "下载"):
        c = command(name, "查询原任务状态及下载结果，不重新生成")
        c.add_argument("task_id", metavar="TASK_ID", help="本站任务编号")
        c.add_argument("--输出目录", help="作品保存目录")
        c.add_argument("--等待秒数", type=float, default=0, help="本地等待秒数，默认0")
    c = command("继续", "恢复本地记录；未知提交按业务标记找回，不重提")
    c.add_argument("record", metavar="RECORD_PATH", help="本地记录 JSON 文件路径")
    c.add_argument("--等待秒数", type=float, default=600, help="本地最多等待秒数，默认600")
    c = command("任务", "按业务标记或来源模型筛选当前密钥任务")
    for flag, help in (("业务标记", "原请求 client_ref"), ("来源模型", "source_model 精确型号"), ("来源字段", "source_field 字段名")):
        c.add_argument("--" + flag, help=help)
    c.add_argument("--页", type=int, default=1, help="来源任务页码")
    c.add_argument("--数量", type=int, default=50, help="每页数量1至100")
    c = command("上传", "上传本地图片或视频参考素材")
    c.add_argument("file", metavar="FILE", help="本地文件路径")
    c.add_argument("--类型", choices=["图片", "视频"], required=True, help="素材类型")
    c.add_argument("--模型", required=True, help="目标精确模型 ID")
    c.add_argument("--保留天数", type=int, choices=[3, 7, 15, 30], default=3, help="素材保留天数")
    command("准备", "可选领取外发前停止身份，不是生成前置")
    c = command("停止", "尝试在外发前停止；不承诺撤销已提交模型生成")
    c.add_argument("--请求标识", required=True, help="原请求 idempotency_key")
    c.add_argument("--路径", required=True, choices=list(ROUTES.values()), help="原生成接口准确路径")
    return p


def dispatch(args, root=ROOT):
    if args.command == "初始化":
        return init_config(root)
    if args.command == "目录" and args.离线:
        manifest = json_read(Path(root) / "docs-manifest.json")
        models = [{"id": model, "kind": page["kind"], "文档": page["path"]} for page in manifest["pages"] for model in page["model_ids"]]
        return {"说明": "内置离线目录；不代表当前密钥权限。", "snapshot_at": manifest["snapshot_at"], "data": models}
    client = Client(root, args.线路)
    cmd = args.command
    if cmd == "目录":
        response = client.request("GET", "/v1/media/models")[0]
        if "data" in response:
            response.pop("models", None)
        elif "models" in response:
            response["data"] = response.pop("models")
        return {"说明": "当前密钥可用目录。", **response}
    if cmd in {"生成", "估价"}:
        parameters = json_read(args.参数文件)
        if not isinstance(parameters, dict) or not parameters.get("model"):
            raise ClientError("参数文件必须是包含精确 model 的 JSON 对象。")
        if cmd == "生成":
            return generate(client, args.类型, parameters, args.输出目录, args.等待秒数, args.不等待, args.请求标识)
        return {"说明": "估价不是最终账单，未发起生成。", "response": client.request("POST", "/v1/media/quote", {"model": parameters["model"], "operation": OPERATIONS[args.类型], "parameters": {k: v for k, v in parameters.items() if k != "model"}})[0]}
    if cmd in {"查询", "下载"}:
        return query_task(client, args.task_id, args.输出目录, args.等待秒数, require_done=cmd == "下载")
    if cmd == "继续":
        return resume(client, args.record, args.等待秒数)
    if cmd == "任务":
        if not 1 <= args.数量 <= 100 or args.页 < 1:
            raise ClientError("数量必须1至100，页码必须大于0。")
        return client.tasks(client_ref=args.业务标记, source_model=args.来源模型, source_field=args.来源字段, page=args.页, limit=args.数量)
    if cmd == "上传":
        return upload(client, args.file, args.类型, args.模型, args.保留天数)
    if cmd == "准备":
        return {"说明": "请保存返回身份，用于同一原请求及停止操作。", "response": client.request("POST", "/v1/media/requests/prepare", {})[0]}
    if cmd == "停止":
        response = client.request("POST", "/v1/media/requests/cancel", {"request_path": args.路径}, headers={"Idempotency-Key": args.请求标识})[0]
        data = response.get("data", {})
        confirmed = data.get("state") == "cancelled_before_dispatch" and data.get("confirmed_not_dispatched") is True
        result = {"说明": "已确认未外发。" if confirmed else "未确认停止：可能已经派发或提交；请查询原任务，本地等待停止不等于撤销。", "confirmed": confirmed, "response": response}
        if not confirmed:
            raise ClientError(result["说明"], code=3, response=response)
        return result


def emit(data):
    print(json.dumps(sanitize(data), ensure_ascii=False))


def configure_streams():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv=None):
    configure_streams()
    try:
        args = parser().parse_args(argv)
        emit({"ok": True, **dispatch(args)})
        return 0
    except ClientError as exc:
        emit({"ok": False, "说明": str(exc), **exc.details})
        return exc.code
    except KeyboardInterrupt:
        emit({"ok": False, "说明": "本地操作已中断，不代表服务端取消。请从 .cache/tasks 的原记录继续查询。"})
        return 130
    except (OSError, ValueError, TypeError) as exc:
        emit({"ok": False, "说明": "本地文件、配置或返回格式处理失败，请检查路径及数据。", "diagnostic": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
