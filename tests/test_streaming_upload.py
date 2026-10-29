import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

from deploy.configure_streaming_upload import configure, patch_source

REDIRECT = """server {
    listen 80;
    server_name img.astrogate.sbs;
    location / { return 301 https://$host$request_uri; }
}
"""
HTTPS = """server {
    listen 443 ssl http2;
    server_name img.astrogate.sbs;
    ssl_certificate /etc/letsencrypt/live/img.astrogate.sbs/fullchain.pem;
    location / {
        proxy_pass http://127.0.0.1:43922;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
    }
}
"""
OTHER = HTTPS.replace("img.astrogate.sbs", "other.example")
SOURCE = REDIRECT + HTTPS + OTHER


class StreamingUploadTests(unittest.TestCase):
    def test_only_target_https_location_changes_and_patch_is_idempotent(self):
        updated = patch_source(SOURCE)
        expected = HTTPS.replace("        proxy_buffering off;", "        proxy_buffering off;\n        proxy_request_buffering off;")
        self.assertEqual(updated, REDIRECT + expected + OTHER)
        self.assertEqual(patch_source(updated), updated)
        self.assertEqual(patch_source(SOURCE.replace("\n", "\r\n")), updated.replace("\n", "\r\n"))

    def test_ambiguous_servers_and_locations_are_rejected(self):
        for source in (SOURCE + HTTPS, SOURCE.replace("        proxy_buffering off;", "        proxy_buffering off;\n    }\n    location / {", 1)):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "exactly one"):
                patch_source(source)

    def test_unexpected_route_or_http_version_is_rejected(self):
        for source in (SOURCE.replace("43922", "43923"), SOURCE.replace("proxy_http_version 1.1;", "proxy_http_version 1.0;"), REDIRECT + OTHER):
            with self.subTest(source=source), self.assertRaises(ValueError):
                patch_source(source)

    def test_duplicate_or_explicit_on_directive_is_rejected(self):
        for directives in ("proxy_request_buffering on;", "proxy_request_buffering off;\n        proxy_request_buffering off;"):
            with self.subTest(directives=directives), self.assertRaisesRegex(ValueError, "request buffering"):
                patch_source(SOURCE.replace("proxy_buffering off;", directives + "\n        proxy_buffering off;", 1))

    def test_configure_backs_up_validates_and_reloads_once(self):
        with TemporaryDirectory() as directory:
            site = Path(directory) / "img.conf"
            site.write_text(SOURCE, encoding="utf-8")
            original = site.read_bytes()
            with patch("deploy.configure_streaming_upload.subprocess.run") as run:
                result = configure(site)
                self.assertTrue(result["changed"])
                self.assertEqual(Path(result["backup"]).read_bytes(), original)
                self.assertEqual(site.read_text(encoding="utf-8"), patch_source(SOURCE))
                self.assertEqual(run.call_args_list, [call(["nginx", "-t"], check=True), call(["systemctl", "reload", "nginx"], check=True)])
                run.reset_mock()
                self.assertEqual(configure(site), {"changed": False})
                run.assert_not_called()

    def test_validation_or_reload_failure_restores_original_and_reloads_it(self):
        for outcomes in ([subprocess.CalledProcessError(1, "nginx"), None, None], [None, subprocess.CalledProcessError(1, "systemctl"), None, None]):
            with self.subTest(outcomes=outcomes), TemporaryDirectory() as directory:
                site = Path(directory) / "img.conf"
                site.write_text(SOURCE, encoding="utf-8")
                original = site.read_bytes()
                with patch("deploy.configure_streaming_upload.subprocess.run", side_effect=outcomes) as run, self.assertRaises(subprocess.CalledProcessError):
                    configure(site)
                self.assertEqual(site.read_bytes(), original)
                self.assertEqual(run.call_args_list[-2:], [call(["nginx", "-t"], check=True), call(["systemctl", "reload", "nginx"], check=True)])
                self.assertEqual(len(list(Path(directory).glob("*.before-streaming-upload-*"))), 1)


if __name__ == "__main__":
    unittest.main()
