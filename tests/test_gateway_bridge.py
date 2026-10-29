import asyncio
import base64
import io
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app.main import Settings, create_app, database_connection

BRIDGE_KEY = "test-only-private-bridge-key-at-least-32-characters"
UPSTREAM_KEY = "test-only-upstream-key"


class GatewayBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        encoded = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(encoded, "PNG")
        self.png = base64.b64encode(encoded.getvalue()).decode()
        self.calls = []
        self.records = []
        self.fail_import = False
        self.fail_image = False
        self.image_count = 1
        self.response_images = None
        self.image_gate = None

    def app(self):
        async def upstream(request):
            self.assertEqual(request.headers["authorization"], "Bearer " + UPSTREAM_KEY)
            body = json.loads(request.content) if request.content else None
            self.calls.append((request.method, request.url.path, body))
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "gpt-image-2"}, {"id": "gpt-text"}]})
            if request.url.path.startswith("/v1/images/"):
                if self.image_gate:
                    entered, release = self.image_gate
                    entered.set()
                    await asyncio.to_thread(release.wait, 5)
                if self.fail_image:
                    return httpx.Response(500, json={"detail": UPSTREAM_KEY})
                images = self.response_images if self.response_images is not None else [self.png] * self.image_count
                return httpx.Response(200, json={"data": [{"b64_json": image} for image in images]})
            if request.method == "GET":
                return httpx.Response(200, json={"items": list(self.records)})
            if request.method == "POST":
                if self.fail_import:
                    return httpx.Response(500, json={"detail": UPSTREAM_KEY})
                for item in body["accounts"]:
                    self.records = [row for row in self.records if row["access_token"] != item["access_token"]]
                    self.records.append(item)
                return httpx.Response(200, json={"added": len(body["accounts"])})
            if request.method == "DELETE":
                self.records = [row for row in self.records if row["access_token"] not in body["tokens"]]
                return httpx.Response(200, json={"deleted": len(body["tokens"])})
            raise AssertionError("Unexpected upstream call")
        return create_app(Settings(Path(self.directory.name), "https://upstream.invalid", UPSTREAM_KEY, False,
                                   httpx.MockTransport(upstream), gateway_bridge_key=BRIDGE_KEY,
                                   gateway_public_origin="https://img.example.test"))

    def headers(self):
        return {"Authorization": "Bearer " + BRIDGE_KEY}

    def test_private_key_required_and_no_customer_cookie_fallback(self):
        with TestClient(self.app()) as client:
            path = "/internal/astrogate/v1/images/generations"
            self.assertEqual(client.post(path, json={"prompt": "draw"}).status_code, 401)
            self.assertEqual(client.post(path, headers={"Authorization": "Bearer " + UPSTREAM_KEY}, json={"prompt": "draw"}).status_code, 401)
            self.assertEqual(client.get("/internal/astrogate/accounts/status").status_code, 401)
            self.assertEqual(self.calls, [])

    def test_images_are_real_private_downloads_without_customer_credit_changes(self):
        with TestClient(self.app()) as client:
            response = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json={"model": "gpt-image-2", "prompt": "a blue logo", "n": 1})
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()["data"][0]
            self.assertEqual(item["b64_json"], self.png)
            path = item["url"].removeprefix("https://img.example.test")
            self.assertEqual(client.get(path).content, base64.b64decode(self.png))
            self.assertEqual(client.get("/gateway-assets/predictable.png").status_code, 404)
            for secret in (BRIDGE_KEY, UPSTREAM_KEY):
                self.assertNotIn(secret, response.text)
            with database_connection(Path(self.directory.name) / "astrogate.db") as db:
                self.assertEqual(db.execute("SELECT count(*) FROM generation_jobs").fetchone()[0], 0)
            self.fail_image = True
            failed = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json={"prompt": "draw"})
            self.assertEqual(failed.status_code, 502)
            self.assertNotIn(UPSTREAM_KEY, failed.text)

    def test_image_references_are_forwarded_without_remote_url_fetches(self):
        with TestClient(self.app()) as client:
            response = client.post("/internal/astrogate/v1/images/edits", headers=self.headers(), json={"prompt": "edit", "images": [{"image_url": "data:image/png;base64," + self.png}]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.calls[-1][2]["images"][0]["b64_json"], self.png)
            denied = client.post("/internal/astrogate/v1/images/edits", headers=self.headers(), json={"prompt": "edit", "images": [{"image_url": "http://127.0.0.1/private"}]})
            self.assertEqual(denied.status_code, 400)

    def test_same_gateway_request_cannot_generate_or_charge_twice(self):
        with TestClient(self.app()) as client:
            payload = {"prompt": "draw a logo", "gateway_request_id": "a" * 64}
            first = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json=payload)
            second = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(first.json(), second.json())
            self.assertEqual(first.headers["x-request-id"], "a" * 64)
            self.assertEqual(len(self.calls), 1)
            mismatch = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json={**payload, "prompt": "different"})
            self.assertEqual(mismatch.status_code, 409)

    def test_extra_upstream_images_return_only_requested_count_and_reuse_same_request(self):
        self.image_count = 3
        payload = {"prompt": "edit", "images": [{"b64_json": self.png}], "gateway_request_id": "e" * 64, "n": 1}
        with TestClient(self.app()) as client:
            path = "/internal/astrogate/v1/images/edits"
            first = client.post(path, headers=self.headers(), json=payload)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(len(first.json()["data"]), 1)
            self.assertEqual(first.json()["data"][0]["b64_json"], self.png)
            second = client.post(path, headers=self.headers(), json=payload)
            self.assertEqual(second.json(), first.json())
            self.assertEqual(len(self.calls), 1)

    def test_extra_outputs_prefer_new_pixels_even_when_reference_encoding_differs(self):
        alternate = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(alternate, "PNG", compress_level=0)
        reference_copy = base64.b64encode(alternate.getvalue()).decode()
        self.assertNotEqual(reference_copy, self.png)
        output = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(output, "PNG")
        generated = base64.b64encode(output.getvalue()).decode()
        self.response_images = [reference_copy, generated, self.png]
        payload = {"prompt": "edit", "images": [{"b64_json": self.png}], "gateway_request_id": "f" * 64, "n": 1}
        with TestClient(self.app()) as client:
            path = "/internal/astrogate/v1/images/edits"
            first = client.post(path, headers=self.headers(), json=payload)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual([item["b64_json"] for item in first.json()["data"]], [generated])
            self.assertEqual(client.post(path, headers=self.headers(), json=payload).json(), first.json())
            self.assertEqual(len(self.calls), 1)
            fallback = client.post(path, headers=self.headers(), json={**payload, "gateway_request_id": "1" * 64, "n": 2})
            self.assertEqual([item["b64_json"] for item in fallback.json()["data"]], [generated, reference_copy])
            self.response_images = [self.png]
            single = client.post(path, headers=self.headers(), json={**payload, "gateway_request_id": "2" * 64})
            self.assertEqual(single.json()["data"][0]["b64_json"], self.png)

    def test_image_job_lookup_is_read_only_and_returns_saved_result(self):
        request_id = "b" * 64
        path = "/internal/astrogate/v1/images/jobs/" + request_id
        with TestClient(self.app()) as client:
            self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get(path, headers=self.headers()).json(), {"status": "missing"})
            self.assertFalse((Path(self.directory.name) / "gateway-bridge.sqlite3").exists())
            self.assertEqual(client.get(path[:-1] + "z", headers=self.headers()).status_code, 400)
            first = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(),
                                json={"prompt": "blue logo", "gateway_request_id": request_id})
            recovered = client.get(path, headers=self.headers())
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(recovered.json(), {"status": "succeeded", "result": first.json()})
            self.assertEqual(len(self.calls), 1)
            for secret in (BRIDGE_KEY, UPSTREAM_KEY):
                self.assertNotIn(secret, recovered.text)

    def test_image_job_lookup_distinguishes_unknown_expired_and_unsafe_results(self):
        unknown_id, done_id = "c" * 64, "d" * 64
        path = "/internal/astrogate/v1/images/jobs/"
        with TestClient(self.app()) as client:
            self.fail_image = True
            self.assertEqual(client.post("/internal/astrogate/v1/images/generations", headers=self.headers(),
                json={"prompt": "unknown", "gateway_request_id": unknown_id}).status_code, 502)
            self.assertEqual(client.get(path + unknown_id, headers=self.headers()).json(), {"status": "unknown"})
            self.fail_image = False
            saved = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(),
                json={"prompt": "done", "gateway_request_id": done_id}).json()
            with patch("app.gateway_bridge.MAX_OUTPUT", 10):
                self.assertEqual(client.get(path + done_id, headers=self.headers()).json(), {"status": "unavailable"})
            filename = saved["data"][0]["url"].rsplit("/", 1)[1]
            (Path(self.directory.name) / "gateway-assets" / filename).unlink()
            self.assertEqual(client.get(path + done_id, headers=self.headers()).json(), {"status": "expired"})
            with closing(sqlite3.connect(Path(self.directory.name) / "gateway-bridge.sqlite3")) as db, db:
                db.execute("UPDATE image_jobs SET result=? WHERE id=?", (json.dumps({"data": [{"filename": "../../secret"}]}), done_id))
            self.assertEqual(client.get(path + done_id, headers=self.headers()).json(), {"status": "unavailable"})
            self.assertEqual(len(self.calls), 2)

    def test_image_job_is_pending_only_while_its_upstream_request_is_active(self):
        request_id = "a" * 64
        self.image_gate = threading.Event(), threading.Event()
        with TestClient(self.app()) as client, ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, "/internal/astrogate/v1/images/generations", headers=self.headers(),
                                 json={"prompt": "blue logo", "gateway_request_id": request_id})
            try:
                self.assertTrue(self.image_gate[0].wait(3))
                state = client.get("/internal/astrogate/v1/images/jobs/" + request_id, headers=self.headers()).json()
                self.assertEqual(state, {"status": "pending"})
            finally:
                self.image_gate[1].set()
            self.assertEqual(future.result(timeout=5).status_code, 200)
            self.assertEqual(client.get("/internal/astrogate/v1/images/jobs/" + request_id,
                                        headers=self.headers()).json()["status"], "succeeded")

    def test_upstream_and_result_failures_remain_unknown_and_never_resubmit(self):
        with TestClient(self.app()) as client:
            for index, fault in enumerate(("upstream", "empty", "invalid"), 1):
                with self.subTest(fault=fault):
                    request_id = str(index) * 64
                    self.fail_image = fault == "upstream"
                    self.response_images = [] if fault == "empty" else ["not-an-image"] if fault == "invalid" else None
                    payload = {"prompt": "blue logo", "gateway_request_id": request_id}
                    before = len(self.calls)
                    response = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json=payload)
                    self.assertEqual(response.status_code, 502)
                    self.assertNotIn(UPSTREAM_KEY, response.text)
                    self.assertEqual(client.get("/internal/astrogate/v1/images/jobs/" + request_id,
                                                headers=self.headers()).json(), {"status": "unknown"})
                    self.assertEqual(client.post("/internal/astrogate/v1/images/generations", headers=self.headers(),
                                                 json=payload).status_code, 409)
                    self.assertEqual(len(self.calls), before + 1)
                    with closing(sqlite3.connect(Path(self.directory.name) / "gateway-bridge.sqlite3")) as db:
                        self.assertEqual(db.execute("SELECT status,result FROM image_jobs WHERE id=?", (request_id,)).fetchone(),
                                         ("unknown", None))

    def test_result_save_failure_is_unknown_and_never_resubmits(self):
        request_id = "4" * 64
        payload = {"prompt": "blue logo", "gateway_request_id": request_id}
        with TestClient(self.app()) as client:
            with patch("pathlib.Path.write_bytes", side_effect=OSError("test disk failure")), self.assertRaises(OSError):
                client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json=payload)
            self.assertEqual(client.get("/internal/astrogate/v1/images/jobs/" + request_id,
                                        headers=self.headers()).json(), {"status": "unknown"})
            self.assertEqual(client.post("/internal/astrogate/v1/images/generations", headers=self.headers(),
                                         json=payload).status_code, 409)
            self.assertEqual(len(self.calls), 1)

    def test_gateway_model_discovery_and_image_streams(self):
        with TestClient(self.app()) as client:
            models = client.get("/internal/astrogate/v1/models", headers=self.headers())
            self.assertEqual([row["id"] for row in models.json()["data"]], ["gpt-image-2"])
            response = client.post("/internal/astrogate/v1/images/generations", headers=self.headers(), json={"prompt": "blue logo", "stream": True})
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            event = json.loads(response.text.split("data: ", 1)[1])
            self.assertEqual(event["type"], "image_generation.completed")
            self.assertEqual(event["b64_json"], self.png)

    def test_sync_is_idempotent_access_token_only_and_does_not_delete_other_accounts(self):
        self.records = [{"access_token": "independent-web-token", "refresh_token": "independent-refresh"},
                        {"access_token": "old-managed", "astrogate_managed_by": "astrogate-gateway", "astrogate_source_id": "42"}]
        body = {"complete": True, "accounts": [{"source_id": "42", "access_token": "new-managed", "refresh_token": "never-copy-this-refresh", "email": "fixture@example.test"}]}
        with TestClient(self.app()) as client:
            first = client.post("/internal/astrogate/accounts/sync", headers=self.headers(), json=body)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["synced"], 1)
            self.assertEqual(first.json()["removed"], 1)
            imported = next(row for row in self.records if row["access_token"] == "new-managed")
            self.assertEqual(imported["refresh_token"], "")
            self.assertNotIn("never-copy-this-refresh", json.dumps(self.calls))
            self.assertIn("independent-web-token", {row["access_token"] for row in self.records})
            second = client.post("/internal/astrogate/accounts/sync", headers=self.headers(), json=body)
            self.assertEqual(second.json()["synced"], 0)
            self.assertEqual(second.json()["skipped"], 1)
            empty = client.post("/internal/astrogate/accounts/sync", headers=self.headers(), json={"complete": True, "accounts": []})
            self.assertEqual(empty.json()["removed"], 1)
            self.assertEqual(len(self.records), 1)
            for token in ("independent-web-token", "new-managed", "old-managed"):
                self.assertNotIn(token, first.text)

    def test_failed_import_keeps_old_credentials_and_reports_sanitized_error(self):
        self.records = [{"access_token": "old-managed", "astrogate_managed_by": "astrogate-gateway", "astrogate_source_id": "42"}]
        self.fail_import = True
        with TestClient(self.app()) as client:
            response = client.post("/internal/astrogate/accounts/sync", headers=self.headers(), json={"complete": True, "accounts": [{"source_id": "42", "access_token": "new-managed"}]})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(UPSTREAM_KEY, response.text)
            self.assertEqual(self.records[0]["access_token"], "old-managed")
            self.assertFalse(any(method == "DELETE" for method, _, _ in self.calls))
            self.assertFalse(client.get("/internal/astrogate/accounts/status", headers=self.headers()).json()["running"])
