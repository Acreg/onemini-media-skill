"""全部生成测试使用本地 HTTP 或隔离响应，不调用收费服务。"""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import onemini as media

TESTS = Path(__file__).resolve().parent
FAKE_KEY = "test-only-secret-do-not-display"
M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 12
ISOM_AUDIO = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 12
PNG = b"\x89PNG\r\n\x1a\n" + b"test-image" * 12
MP3 = b"ID3" + b"audio" * 14000


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(json.dumps(body).encode() if not isinstance(body, bytes) else body)
        self.status = status
        self.headers = headers or {}


class Transport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def open(self, req, **kwargs):
        self.calls.append(req)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return Response(outcome)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TESTS)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"ONEMINI_API_KEY": FAKE_KEY, "ONEMINI_BASE_URL": media.LINES["主站"]})
        self.env.start()
        self.client = media.Client(self.root)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def transport(self, outcomes):
        transport = Transport(outcomes)
        self.client.opener = transport
        return transport

    def record(self):
        return list((self.root / ".cache" / "tasks").glob("*.json"))[0]

    def test_init_does_not_overwrite_or_connect(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("不得联网")):
            media.init_config(self.root)
            path = self.root / media.ENV_NAME
            self.assertIn("ONEMINI_API_KEY=", path.read_text(encoding="utf-8"))
            path.write_text("ONEMINI_API_KEY=keep\n", encoding="utf-8")
            media.init_config(self.root)
            self.assertEqual(path.read_text(), "ONEMINI_API_KEY=keep\n")

    def test_configuration_root_not_cwd_and_environment_override(self):
        (self.root / media.ENV_NAME).write_text("ONEMINI_API_KEY=file-secret\nONEMINI_BASE_URL=https://api-hk.onemini.top\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            key, base = media.load_config(self.root)
            self.assertEqual(key, "file-secret")
            self.assertEqual(base, media.LINES["香港"])
        self.assertEqual(media.load_config(self.root)[0], FAKE_KEY)
        self.assertEqual(media.load_config(self.root, "亚太")[1], media.LINES["亚太"])

    def test_untrusted_auth_host_rejected_before_network(self):
        with patch.dict(os.environ, {"ONEMINI_BASE_URL": "https://evil.example"}):
            with self.assertRaises(media.ClientError):
                media.Client(self.root)
        transport = self.transport([])
        self.client.base = "http://api.onemini.top"
        with self.assertRaises(media.ClientError):
            self.client.request("GET", "/v1/media/models")
        self.assertEqual(transport.calls, [])

    def test_sync_base64_and_transparent_parameters(self):
        transport = self.transport([{"data": [{"b64_json": base64.b64encode(PNG).decode()}]}])
        parameters = {"model": "gpt-image-2.6-ext", "prompt": "猫", "custom": {"flag": False, "array": [1, "2"]}, "seed": 0}
        result = media.generate(self.client, "图片", parameters, output_dir=self.root / "作品", wait=0)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(Path(result["files"][0]["path"]).read_bytes(), PNG)
        self.assertEqual(result["files"][0]["mime_type"], "image/png")
        sent = json.loads(transport.calls[0].data)
        for key, value in parameters.items():
            self.assertEqual(sent[key], value)
        self.assertTrue(sent["client_ref"])
        record = media.json_read(self.record())
        self.assertEqual(transport.calls[0].get_header("Idempotency-key"), record["idempotency_key"])
        self.assertEqual(len(transport.calls), 1)

    def test_async_queued_running_success_and_multi_outputs(self):
        encoded = base64.b64encode(PNG).decode()
        transport = self.transport([
            {"data": {"task_id": "site-task", "status": "queued"}},
            {"data": {"task_id": "site-task", "status": "running", "progress": 30}},
            {"data": {"task_id": "site-task", "status": "succeeded", "result": {"images": [{"b64_json": encoded}, {"b64_json": base64.b64encode(PNG + b"second").decode()}]}}},
        ])
        with patch.object(media, "POLL_INTERVAL", 0):
            result = media.generate(self.client, "图片", {"model": "m"}, output_dir=self.root / "作品", wait=10)
        self.assertEqual(len(result["files"]), 2)
        self.assertEqual([r.method for r in transport.calls], ["POST", "GET", "GET"])
        self.assertTrue(all("/v1/media/tasks/site-task" in r.full_url for r in transport.calls[1:]))

    def test_task_failure_is_error_with_record_and_redaction(self):
        self.transport([{"data": {"task_id": "site-fail", "status": "failed", "error": {"message": FAKE_KEY + " 不可用"}}}])
        with self.assertRaises(media.ClientError) as caught:
            media.generate(self.client, "视频", {"model": "m"})
        self.assertEqual(caught.exception.details["status"], "failed")
        self.assertNotIn(FAKE_KEY, self.record().read_text(encoding="utf-8"))
        self.assertEqual(media.json_read(self.record())["status"], "failed")

    def test_wait_timeout_is_not_cancel_and_resume_get_only(self):
        transport = self.transport([{"data": {"task_id": "long", "status": "running"}}])
        with patch.object(media.time, "monotonic", side_effect=[0, 1]), self.assertRaises(media.ClientError) as caught:
            media.generate(self.client, "视频", {"model": "m"}, output_dir=self.root / "作品", wait=0.001)
        self.assertIn("没有被取消", str(caught.exception))
        self.assertEqual(media.json_read(self.record())["status"], "running")
        transport = self.transport([{"data": {"task_id": "long", "status": "succeeded", "result": {"b64_json": base64.b64encode(PNG).decode()}}}])
        result = media.resume(self.client, self.record(), wait=0)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([r.method for r in transport.calls], ["GET"])

    def test_unknown_post_not_retried_then_client_ref_lookup(self):
        transport = self.transport([urllib.error.URLError(socket.timeout("timed out " + FAKE_KEY))])
        with self.assertRaises(media.ClientError):
            media.generate(self.client, "音乐", {"model": "suno"}, output_dir=self.root / "作品", wait=0)
        self.assertEqual(len(transport.calls), 1)
        original = media.json_read(self.record())
        self.assertEqual(original["status"], "submit_unknown")
        self.assertNotIn(FAKE_KEY, self.record().read_text(encoding="utf-8"))
        self.client.base = media.LINES["香港"]
        transport = self.transport([{"data": [{"task_id": "recovered", "status": "succeeded", "result": {"b64_json": base64.b64encode(PNG).decode()}}]}])
        result = media.resume(self.client, self.record(), 0)
        self.assertEqual(result["task_id"], "recovered")
        req = transport.calls[0]
        self.assertTrue(req.full_url.startswith(original["base_url"]))
        self.assertIn("client_ref=" + original["client_ref"], req.full_url)
        self.assertEqual(req.method, "GET")

    def test_unknown_lookup_no_match_never_resubmits(self):
        transport = self.transport([urllib.error.URLError("timeout")])
        with self.assertRaises(media.ClientError):
            media.generate(self.client, "视频", {"model": "m"})
        transport = self.transport([{"data": []}])
        with self.assertRaises(media.ClientError):
            media.resume(self.client, self.record(), 0)
        self.assertEqual([r.method for r in transport.calls], ["GET"])

    def test_record_exists_before_post_and_keyboard_interrupt(self):
        def interrupt(req, **kwargs):
            record = media.json_read(self.record())
            self.assertTrue(record["idempotency_key"])
            self.assertTrue(record["client_ref"])
            self.assertEqual(record["status"], "submit_unknown")
            raise KeyboardInterrupt()
        self.client.opener.open = interrupt
        with self.assertRaises(KeyboardInterrupt):
            media.generate(self.client, "视频", {"model": "m"})
        self.assertTrue(self.record().is_file())

    def test_http_errors_top_level_nested_and_string(self):
        for payload, expected_code in [({"code": "API_KEY_REQUIRED", "message": "未提供 API 密钥"}, "API_KEY_REQUIRED"),
                                       ({"error": {"code": "BAD_FIELD", "message": "字段错误 " + FAKE_KEY}}, "BAD_FIELD"),
                                       ({"error": "错误 " + FAKE_KEY}, None)]:
            error = urllib.error.HTTPError("https://api.onemini.top/v1/media/models", 401, "", {}, io.BytesIO(json.dumps(payload).encode()))
            self.transport([error])
            with self.assertRaises(media.ClientError) as caught:
                self.client.request("GET", "/v1/media/models")
            self.assertEqual(caught.exception.details["error_code"], expected_code)
            self.assertNotIn(FAKE_KEY, json.dumps(caught.exception.details))
            if expected_code == "API_KEY_REQUIRED":
                self.assertIn("未提供", str(caught.exception))
                self.assertNotIn("密钥无效", str(caught.exception).split("；")[0])

    def test_http_503_no_retry_saves_unknown_and_header(self):
        error = urllib.error.HTTPError("https://api.onemini.top/v1/videos/generations", 503, "", {"X-Media-Task-ID": "saved"}, io.BytesIO(b'{"error":"unavailable"}'))
        transport = self.transport([error])
        with self.assertRaises(media.ClientError):
            media.generate(self.client, "视频", {"model": "m"})
        record = media.json_read(self.record())
        self.assertEqual(record["status"], "submit_unknown")
        self.assertEqual(record["task_id"], "saved")
        self.assertEqual(len(transport.calls), 1)

    def test_business_error_not_success(self):
        self.transport([{"code": "MODEL_NOT_ALLOWED", "message": "模型不可用"}])
        with self.assertRaises(media.ClientError) as caught:
            self.client.request("GET", "/v1/media/models")
        self.assertEqual(caught.exception.details["error_code"], "MODEL_NOT_ALLOWED")

    def test_success_without_result_is_not_empty_success(self):
        self.transport([{"data": {"task_id": "empty", "status": "succeeded", "result": {}}}])
        with self.assertRaises(media.ClientError):
            media.generate(self.client, "视频", {"model": "m"})

    def test_no_wait_and_header_only_task(self):
        transport = self.transport([])
        transport.outcomes = []
        transport.open = lambda req, **kw: Response({}, 202, {"X-Media-Task-ID": "header-task"})
        result = media.generate(self.client, "视频", {"model": "m"}, no_wait=True)
        self.assertEqual(result["task_id"], "header-task")
        self.assertEqual(result["status"], "queued")

    def test_catalog_deduplicates_and_optional_operations(self):
        args = media.parser().parse_args(["目录"])
        self.transport([{"data": [{"id": "m"}], "models": [{"id": "m"}]}])
        with patch.object(media, "Client", return_value=self.client):
            result = media.dispatch(args, self.root)
        self.assertNotIn("models", result)
        path = self.root / "p.json"
        path.write_text('{"model":"suno","prompt":"song","instrumental":true}')
        transport = self.transport([{"estimated_amount": 1}, {"data": {"idempotency_key": "prepared"}}, {"data": {"state": "dispatch_started"}}])
        with patch.object(media, "Client", return_value=self.client):
            media.dispatch(media.parser().parse_args(["估价", "--类型", "音乐", "--参数文件", str(path)]), self.root)
            media.dispatch(media.parser().parse_args(["准备"]), self.root)
            with self.assertRaises(media.ClientError):
                media.dispatch(media.parser().parse_args(["停止", "--请求标识", "prepared", "--路径", "/v1/music/generations"]), self.root)
        quote = json.loads(transport.calls[0].data)
        self.assertEqual(quote, {"model": "suno", "operation": "music", "parameters": {"prompt": "song", "instrumental": True}})
        self.assertEqual(json.loads(transport.calls[2].data), {"request_path": "/v1/music/generations"})
        self.assertTrue(all("/generations" not in r.full_url for r in transport.calls))

    def test_task_filters_and_single_part_upload(self):
        transport = self.transport([{"data": []}, {"url": "https://example.test/image"}])
        self.client.tasks(client_ref="中文 ref", source_model="m", source_field="draft_task_id", page=2, limit=3)
        self.assertIn("source_field=draft_task_id", transport.calls[0].full_url)
        file = self.root / "reference.png"
        file.write_bytes(PNG)
        response = media.upload(self.client, file, "图片", "m", 3)
        self.assertEqual(response["url"], "https://example.test/image")
        req = transport.calls[1]
        self.assertIn("retention_days=3", req.full_url)
        self.assertEqual(req.data.count(b'name="file"'), 1)
        self.assertIn(PNG, req.data)
        self.assertIn("boundary=onemini-", req.get_header("Content-type"))

    def test_secret_sanitized_everywhere_and_json_stdout(self):
        raw = {"error": "Bearer " + FAKE_KEY, "Authorization": "x", "url": "https://host/?s=" + FAKE_KEY, "nested": [FAKE_KEY]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            media.emit(raw)
        self.assertNotIn(FAKE_KEY, out.getvalue())
        json.loads(out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(media.main(["生成"]), 2)

    def test_help_chinese_and_all_contract_commands(self):
        p = media.parser()
        help = p.format_help()
        for command in ("初始化", "目录", "生成", "查询", "继续", "任务", "上传", "下载", "估价", "准备", "停止"):
            self.assertIn(command, help)
        self.assertIn("用法", help)
        self.assertNotIn("show this help message", help)
        self.assertEqual(p.parse_args(["--线路", "香港", "目录"]).线路, "香港")
        self.assertEqual(p.parse_args(["目录", "--线路", "亚太"]).线路, "亚太")

    def test_default_outputs_use_task_workspace(self):
        transport = self.transport([{"data": [{"b64_json": base64.b64encode(PNG).decode()}]}])
        workspace = self.root / "任务工作区"
        workspace.mkdir()
        with patch.object(media.Path, "cwd", return_value=workspace):
            result = media.generate(self.client, "图片", {"model": "m"})
        self.assertTrue(Path(result["output_dir"]).is_relative_to(workspace / "outputs"))
        self.assertEqual(len(transport.calls), 1)

    def test_input_metadata_is_not_generated_output(self):
        payload = {"data": {"task_id": "empty-result", "status": "succeeded", "input_assets": [{"url": "https://example.test/reference.png"}], "metadata": {"image_url": "https://example.test/ref.png"}, "result": {}}}
        self.assertEqual(media.assets_from(payload), [])
        self.transport([payload])
        with self.assertRaises(media.ClientError):
            media.generate(self.client, "图片", {"model": "m"}, output_dir=self.root / "作品")

    def test_music_lyrics_saved_with_artifacts(self):
        payload = {"data": {"task_id": "lyrics", "status": "succeeded", "result": {"audios": [{"b64_json": base64.b64encode(MP3).decode(), "lyrics": "[Verse]\n月光照在海上"}], "metadata": {"image_url": "https://example.test/input.png"}}}}
        self.transport([payload])
        result = media.generate(self.client, "音乐", {"model": "suno"}, output_dir=self.root / "作品")
        self.assertEqual(len(result["files"]), 2)
        lyrics = next(f for f in result["files"] if f["mime_type"] == "text/plain")
        self.assertEqual(Path(lyrics["path"]).read_text(encoding="utf-8"), "[Verse]\n月光照在海上")

    def test_data_uri_m4a_and_shared_mp4_preserve_audio(self):
        for content in (M4A, ISOM_AUDIO):
            with self.subTest(brand=content[8:12]):
                url = "data:audio/mp4;base64," + base64.b64encode(content).decode()
                files = media.download_assets({"data": [{"url": url}]}, self.root / "作品")
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0]["mime_type"], "audio/mp4")
                self.assertEqual(Path(files[0]["path"]).suffix, ".m4a")
                self.assertEqual(Path(files[0]["path"]).read_bytes(), content)
        self.assertEqual(media.sniff_mime(M4A), "audio/mp4")

    def test_definite_png_overrides_incorrect_mp4_audio_declaration(self):
        url = "data:audio/mp4;base64," + base64.b64encode(PNG).decode()
        files = media.download_assets({"url": url}, self.root / "作品")
        self.assertEqual(files[0]["mime_type"], "image/png")
        self.assertEqual(Path(files[0]["path"]).suffix, ".png")

    def test_lyrics_only_kept_but_not_reported_as_media_delivery(self):
        self.transport([{"data": {"task_id": "lyrics-only", "status": "succeeded", "result": {"lyrics": "只有歌词，没有返回音乐"}}}])
        with self.assertRaises(media.ClientError) as caught:
            media.generate(self.client, "音乐", {"model": "suno"}, output_dir=self.root / "作品")
        self.assertIn("未返回 URL/Base64 媒体产物", str(caught.exception))
        record = media.json_read(self.record())
        self.assertEqual(len(record["files"]), 1)
        self.assertEqual(Path(record["files"][0]["path"]).read_text(encoding="utf-8"), "只有歌词，没有返回音乐")
        self.assertEqual(caught.exception.details["files"], record["files"])
        self.assertEqual(caught.exception.details["task_id"], "lyrics-only")

    def test_successful_resume_refreshes_task_not_expired_cached_url(self):
        record_path = self.root / "record.json"
        media.json_write(record_path, {"base_url": media.LINES["主站"], "task_id": "refresh", "status": "succeeded", "files": [], "output_dir": str(self.root / "作品"), "response": {"data": {"task_id": "refresh", "status": "succeeded", "result": {"url": "https://example.test/expired"}}}})
        transport = self.transport([{"data": {"task_id": "refresh", "status": "succeeded", "result": {"b64_json": base64.b64encode(PNG).decode()}}}])
        result = media.resume(self.client, record_path, 0)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([r.method for r in transport.calls], ["GET"])

    def test_retry_after_returned_without_retry(self):
        error = urllib.error.HTTPError("https://api.onemini.top/v1/media/models", 429, "", {"Retry-After": "30"}, io.BytesIO(b'{"code":"RATE_LIMIT","message":"slow"}'))
        transport = self.transport([error])
        with self.assertRaises(media.ClientError) as caught:
            self.client.request("GET", "/v1/media/models")
        self.assertEqual(caught.exception.details["retry_after"], "30")
        self.assertEqual(len(transport.calls), 1)

    def test_offline_catalog_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            result = media.dispatch(media.parser().parse_args(["目录", "--离线"]), media.ROOT)
        self.assertEqual(len(result["data"]), 122)


class DownloadHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                cls.calls.append((self.path, dict(self.headers)))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/audio")
                    self.end_headers()
                elif self.path == "/html":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html>expired</html>")
                else:
                    data = {"/image": PNG, "/m4a": M4A, "/isom-audio": ISOM_AUDIO}.get(self.path, MP3)
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mp4" if self.path in {"/m4a", "/isom-audio"} else "application/octet-stream")
                    self.send_header("Content-Disposition", 'attachment; filename="../../escape.exe"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                cls.calls.append((self.path, dict(self.headers)))
                self.send_response(307)
                self.send_header("Location", "/replayed")
                self.end_headers()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)
        media.SECRETS.add(FAKE_KEY)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TESTS)
        self.root = Path(self.temp.name)
        self.calls.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_multi_audio_cover_and_redirect_no_authorization(self):
        body = {"data": {"result": {"audios": [{"url": [self.base + "/redirect", self.base + "/audio2"]}], "music": [{"audio_url": self.base + "/redirect"}], "cover_url": self.base + "/image"}}}
        files = media.download_assets(body, self.root)
        self.assertEqual(len(files), 3)
        self.assertEqual({f["mime_type"] for f in files}, {"audio/mpeg", "image/png"})
        self.assertTrue(all(Path(f["path"]).parent == self.root for f in files))
        self.assertTrue(all("escape" not in f["path"] for f in files))
        self.assertTrue(all("Authorization" not in h for _, h in self.calls))
        self.assertTrue(all("Cookie" not in h for _, h in self.calls))
        self.assertEqual(media.download_assets(body, self.root, files), files)

    def test_http_m4a_and_shared_mp4_preserve_audio(self):
        for path, content in (("/m4a", M4A), ("/isom-audio", ISOM_AUDIO)):
            with self.subTest(path=path):
                files = media.download_assets({"url": self.base + path}, self.root)
                self.assertEqual(files[0]["mime_type"], "audio/mp4")
                self.assertEqual(Path(files[0]["path"]).suffix, ".m4a")
                self.assertEqual(Path(files[0]["path"]).read_bytes(), content)
        self.assertTrue(all("Authorization" not in headers for _, headers in self.calls))

    def test_api_redirect_is_never_replayed(self):
        opener = urllib.request.build_opener(media.NoAPIRedirect())
        with self.assertRaises(media.ClientError):
            opener.open(urllib.request.Request(self.base + "/submit", data=b"{}", method="POST"), timeout=3)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "/submit")

    def test_bad_download_addresses_and_secret_redirect_rejected(self):
        for url in ("file:///etc/passwd", "https://user:password@example.test/file", "https://example.test/" + FAKE_KEY):
            with self.assertRaises(media.ClientError):
                media.validate_download_url(url)
        with self.assertRaises(media.ClientError):
            media.DownloadRedirect().redirect_request(urllib.request.Request(self.base), None, 302, "", {}, "https://example.test/?api_key=" + FAKE_KEY)

    def test_third_party_signature_parameters_allowed(self):
        url = self.base + "/audio?access_token=third-party-signature&api_key=third-party-download-token"
        self.assertEqual(media.validate_download_url(url), url)
        files = media.download_assets({"url": url}, self.root)
        self.assertEqual(len(files), 1)
        self.assertNotIn("Authorization", self.calls[0][1])

    def test_expired_link_partial_progress_saved_and_temp_cleaned(self):
        with self.assertRaises(media.ClientError) as caught:
            media.download_assets({"result": {"audios": [{"url": [self.base + "/audio", self.base + "/html"]}]}}, self.root)
        self.assertEqual(len(caught.exception.details["files"]), 1)
        self.assertEqual(list(self.root.glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
