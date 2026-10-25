import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from fastapi.testclient import TestClient

from app.main import Settings, create_app, database_connection


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlYb4wAAAAASUVORK5CYII="
PASSWORD = "secure-password-42"
IMAGE_25 = "gpt-image-2.5-flare"


class ModelAndPromptTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.mailbox = {}
        self.image_calls = []
        self.text_calls = []

    def app(self, models=("gpt-image-2",), credits=5, image_status=200, text_status=200):
        def image_upstream(request):
            if request.method == "GET" and request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": model} for model in models]})
            self.image_calls.append(json.loads(request.content))
            if image_status != 200:
                return httpx.Response(image_status, json={"error": {"message": "image provider failed"}})
            return httpx.Response(200, json={"data": [{"b64_json": PNG}]})

        def text_upstream(request):
            self.text_calls.append(request)
            if text_status != 200:
                return httpx.Response(text_status, json={"error": {"message": "text provider unavailable"}})
            if "仅返回 JSON 字符串数组" in json.loads(request.content)["messages"][0]["content"]:
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps([f"灵感主题{i}" for i in range(12)], ensure_ascii=False)}}]})
            return httpx.Response(200, json={"choices": [{"message": {"content": "星空下的古城，宽画幅构图，层次清晰，柔和月光。"}}]})

        return create_app(Settings(
            Path(self.directory.name), "https://images.example", "image-secret", False,
            httpx.MockTransport(image_upstream), "operator", PASSWORD, credits,
            mail_sender=lambda email, code: self.mailbox.update({email: code}),
            text_api_base="https://text.example/v1", text_api_key="text-secret",
            text_model="gpt-5.6-luna", text_transport=httpx.MockTransport(text_upstream),
        ))

    def register(self, client):
        email = "artist@example.com"
        self.assertEqual(client.post("/api/auth/email-code", json={"email": email}).status_code, 200)
        response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "code": self.mailbox[email]})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["user"]

    def test_unlisted_image_25_cannot_charge_or_reach_image_upstream(self):
        with TestClient(self.app(models=("gpt-image-2",))) as client:
            self.register(client)
            models = {item["id"]: item for item in client.get("/api/images/models").json()["items"]}
            self.assertTrue(models["gpt-image-2"]["available"])
            self.assertEqual(models["gpt-image-2"]["label"], "GPT Image 2.5 · 标准")
            self.assertEqual(models["gpt-image-2"]["credits_cost"], 1)
            self.assertFalse(models[IMAGE_25]["available"])
            self.assertEqual(models[IMAGE_25]["credits_cost"], 5)
            self.assertEqual(models[IMAGE_25]["price_rmb"], "0.25")
            response = client.post("/api/images/generations", json={"prompt": "星空下的古城", "model": IMAGE_25})
            self.assertIn(response.status_code, (400, 503), response.text)
            self.assertEqual(client.get("/api/wallet").json()["credits"], 5)
            self.assertEqual(self.image_calls, [])

    def test_available_image_25_charges_five_and_records_actual_cost(self):
        with TestClient(self.app(models=("gpt-image-2", IMAGE_25))) as client:
            self.register(client)
            models = {item["id"]: item for item in client.get("/api/images/models").json()["items"]}
            self.assertTrue(models[IMAGE_25]["available"])
            response = client.post("/api/images/generations", json={"prompt": "星空下的古城", "model": IMAGE_25})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["credits"], 0)
            self.assertEqual(response.json()["item"]["credits_cost"], 5)
            self.assertEqual(response.json()["item"]["model"], IMAGE_25)
            self.assertEqual(self.image_calls[0]["model"], IMAGE_25)
            self.assertEqual(client.get("/api/wallet").json()["ledger"][0]["amount"], -5)

    def test_image_25_failure_refunds_five_credits(self):
        with TestClient(self.app(models=("gpt-image-2", IMAGE_25), image_status=429)) as client:
            self.register(client)
            response = client.post("/api/images/generations", json={"prompt": "星空下的古城", "model": IMAGE_25})
            self.assertEqual(response.status_code, 429, response.text)
            wallet = client.get("/api/wallet").json()
            self.assertEqual(wallet["credits"], 5)
            self.assertEqual([item["amount"] for item in wallet["ledger"][:2]], [5, -5])

    def test_restart_refunds_pending_image_25_once(self):
        with TestClient(self.app()) as client:
            user = self.register(client)
        with database_connection(Path(self.directory.name) / "astrogate.db") as db:
            db.execute("INSERT INTO generation_jobs (id, user_id, request_key, fingerprint, status, created_at, credits_cost) VALUES ('interrupted25', ?, 'key25', 'fingerprint', 'pending', '2026-10-20', 5)", (user["id"],))
        for _ in range(2):
            with TestClient(self.app()) as client:
                self.assertEqual(client.post("/api/auth/login", json={"identifier": "artist@example.com", "password": PASSWORD}).status_code, 200)
                self.assertEqual(client.get("/api/wallet").json()["credits"], 10)

    def test_prompt_assistant_requires_login_and_keeps_key_server_side(self):
        with TestClient(self.app()) as client:
            self.assertEqual(client.post("/api/prompts/assist", json={"idea": "月光下的古城"}).status_code, 401)
            self.register(client)
            response = client.post("/api/prompts/assist", json={"idea": "月光下的古城"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("古城", response.json()["prompt"])
            self.assertEqual(len(self.text_calls), 1)
            self.assertEqual(self.text_calls[0].headers["authorization"], "Bearer text-secret")
            self.assertEqual(self.text_calls[0].url.path, "/v1/chat/completions")
            self.assertEqual(json.loads(self.text_calls[0].content)["model"], "gpt-5.6-luna")
            self.assertNotIn("text-secret", response.text)
            self.assertNotIn("text-secret", client.get("/studio").text)

    def test_tags_are_ai_generated_and_assist_falls_back_when_text_node_fails(self):
        with TestClient(self.app()) as client:
            self.register(client)
            response = client.post("/api/prompts/tags", json={"category": "scene"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["source"], "ai")
            self.assertEqual(len(response.json()["tags"]), 12)
        self.text_calls.clear()
        with TestClient(self.app(text_status=503)) as client:
            self.assertEqual(client.post("/api/auth/login", json={"identifier": "artist@example.com", "password": PASSWORD}).status_code, 200)
            response = client.post("/api/prompts/assist", json={"idea": "雨夜的未来城市"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["source"], "fallback")
            self.assertIn("雨夜的未来城市", response.json()["prompt"])
            tags = client.post("/api/prompts/tags", json={"category": "character"})
            self.assertEqual(tags.status_code, 200, tags.text)
            self.assertEqual(tags.json()["source"], "fallback")
            self.assertEqual(len(tags.json()["tags"]), 12)
            switched = client.post("/api/prompts/tags", json={"category": "character", "exclude": tags.json()["tags"]})
            self.assertEqual(switched.status_code, 200, switched.text)
            self.assertGreaterEqual(len(set(switched.json()["tags"]) - set(tags.json()["tags"])), 8)
            self.assertEqual(len(self.text_calls), 1, "A failing node must not delay each tag or prompt click")
