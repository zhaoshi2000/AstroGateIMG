import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from deploy.configure_image_timeout import configure


class Opener:
    def __init__(self, apply=True):
        self.timeout = 120
        self.apply = apply
        self.updates = []

    def open(self, request, timeout):
        if request.data:
            value = json.loads(request.data)["image_poll_timeout_secs"]
            self.updates.append(value)
            if self.apply:
                self.timeout = value
        return io.BytesIO(json.dumps({"config": {"image_poll_timeout_secs": self.timeout}}).encode())


class ConfigureImageTimeoutTests(unittest.TestCase):
    def make_root(self, directory, host="127.0.0.1"):
        root = Path(directory)
        (root / "upstream").mkdir()
        (root / ".env").write_text(f"ASTROGATE_UPSTREAM_URL=http://{host}:21873\nASTROGATE_UPSTREAM_API_KEY=test-only\n")
        (root / "upstream/config.json").write_text('{"image_poll_timeout_secs": 120, "other": true}\n')
        return root

    def test_updates_both_configs_and_can_be_repeated(self):
        with TemporaryDirectory() as directory:
            root = self.make_root(directory)
            opener = Opener()
            with patch("deploy.configure_image_timeout.urllib.request.build_opener", return_value=opener):
                result = configure(root, 720)
                self.assertTrue(result["changed"])
                self.assertEqual(opener.timeout, 720)
                self.assertEqual(json.loads((root / "upstream/config.json").read_text()), {"image_poll_timeout_secs": 720, "other": True})
                self.assertEqual(json.loads((Path(result["backup"]) / "runtime.json").read_text())["image_poll_timeout_secs"], 120)
                self.assertEqual(json.loads((Path(result["backup"]) / "config.json").read_text())["image_poll_timeout_secs"], 120)
                self.assertFalse(configure(root, 720)["changed"])
                self.assertEqual(opener.updates, [720])

    def test_failed_runtime_update_restores_source_and_rejects_remote_host(self):
        with TemporaryDirectory() as directory:
            root = self.make_root(directory)
            original = (root / "upstream/config.json").read_bytes()
            opener = Opener(apply=False)
            with patch("deploy.configure_image_timeout.urllib.request.build_opener", return_value=opener), self.assertRaisesRegex(RuntimeError, "did not apply"):
                configure(root, 720)
            self.assertEqual((root / "upstream/config.json").read_bytes(), original)
            self.assertEqual(opener.updates, [720, 120])
        with TemporaryDirectory() as directory:
            root = self.make_root(directory, "example.com")
            with self.assertRaisesRegex(ValueError, "local image upstream"):
                configure(root, 720)
