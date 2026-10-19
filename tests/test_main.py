from __future__ import annotations

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from fastapi.testclient import TestClient

from app.main import Settings, create_app


TEST_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nimage-bytes").decode("ascii")


class AstroGateImageTests(unittest.TestCase):
    def test_registration_login_and_personal_history(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            settings = Settings(
                data_dir=Path(temporary_directory),
                upstream_url="",
                upstream_api_key="",
                cookie_secure=False,
            )
            with TestClient(create_app(settings)) as client:
                credentials = {"username": "creative_user", "password": "secure-password-42"}
                registration = client.post("/api/auth/register", json=credentials)
                self.assertEqual(registration.status_code, 201)
                self.assertEqual(registration.json()["user"]["username"], "creative_user")

                self.assertEqual(client.get("/api/auth/me").status_code, 200)
                self.assertEqual(client.get("/api/images/history").json()["items"], [])
                self.assertEqual(client.post("/api/images/generations", json={"prompt": "test"}).status_code, 503)
                self.assertEqual(client.post("/api/auth/register", json=credentials).status_code, 409)

                self.assertEqual(client.post("/api/auth/logout").status_code, 200)
                self.assertEqual(client.get("/api/auth/me").status_code, 401)
                self.assertEqual(client.post("/api/auth/login", json=credentials).status_code, 200)
                self.assertEqual(client.get("/api/auth/me").json()["user"]["username"], "creative_user")

    def test_registration_validation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            settings = Settings(Path(temporary_directory), "", "", False)
            with TestClient(create_app(settings)) as client:
                response = client.post("/api/auth/register", json={"username": "x", "password": "short"})
                self.assertEqual(response.status_code, 422)

    def test_successful_generation_is_saved_for_the_current_user(self) -> None:
        def upstream_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/images/generations")
            self.assertEqual(request.headers["authorization"], "Bearer upstream-key")
            self.assertEqual(json.loads(request.content)["prompt"], "a glowing observatory")
            return httpx.Response(200, json={"data": [{"b64_json": "aGVsbG8="}]})

        with TemporaryDirectory() as temporary_directory:
            settings = Settings(
                data_dir=Path(temporary_directory),
                upstream_url="https://upstream.example",
                upstream_api_key="upstream-key",
                cookie_secure=False,
                upstream_transport=httpx.MockTransport(upstream_handler),
            )
            with TestClient(create_app(settings)) as client:
                client.post("/api/auth/register", json={"username": "artist_1", "password": "secure-password-42"})
                response = client.post("/api/images/generations", json={"prompt": "a glowing observatory"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["item"]["image_src"].startswith("data:image/png;base64,"))
                history = client.get("/api/images/history").json()["items"]
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["prompt"], "a glowing observatory")

    def test_image_edit_forwards_reference_image_and_saves_mode(self) -> None:
        def upstream_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/images/edits")
            payload = json.loads(request.content)
            self.assertEqual(payload["prompt"], "make it sunset")
            self.assertEqual(payload["images"][0]["b64_json"], TEST_PNG)
            return httpx.Response(200, json={"data": [{"b64_json": "cmVzdWx0"}]})

        with TemporaryDirectory() as temporary_directory:
            settings = Settings(Path(temporary_directory), "https://upstream.example", "upstream-key", False, httpx.MockTransport(upstream_handler))
            with TestClient(create_app(settings)) as client:
                client.post("/api/auth/register", json={"username": "artist_edit", "password": "secure-password-42"})
                response = client.post("/api/images/edits", json={
                    "prompt": "make it sunset",
                    "image_data_url": f"data:image/png;base64,{TEST_PNG}",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["item"]["mode"], "image-to-image")
                self.assertEqual(client.get("/api/images/history").json()["items"][0]["mode"], "image-to-image")

    def test_image_edit_rejects_invalid_reference_image(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            settings = Settings(Path(temporary_directory), "https://upstream.example", "upstream-key", False)
            with TestClient(create_app(settings)) as client:
                client.post("/api/auth/register", json={"username": "artist_bad_image", "password": "secure-password-42"})
                response = client.post("/api/images/edits", json={
                    "prompt": "make it sunset", "image_data_url": "data:image/png;base64,aGVsbG8=",
                })
                self.assertEqual(response.status_code, 400)
                self.assertEqual(client.get("/api/images/history").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
