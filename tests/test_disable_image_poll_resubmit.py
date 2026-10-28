import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from deploy.disable_image_poll_resubmit import (
    BACKEND_OLD,
    CONNECTION_OLD,
    COUNTER,
    NEW,
    OLD,
    SETTINGS,
    TEXT_OLD,
    TLS_OLD,
    TOKEN_OLD,
    patch,
)

SOURCE = '''def generate(stream, account_service, logger, request, codex_model=False):
    MAX_TEXT_REPLY_RETRIES = 3
    MAX_TLS_RETRIES = 3
    MAX_CONN_TIMEOUT_RETRIES = 3
''' + SETTINGS + '''    text_reply_retry_count = 0
    tls_retry_count = 0
    conn_timeout_retry_count = 0
''' + COUNTER + '''    account_email = "sample"
    token = "sample"
    emitted_for_token = False
    index = 1
    while True:
''' + BACKEND_OLD + '''            return stream(backend)
''' + OLD + '''        except ImageContentPolicyError:
            raise
        except ImageGenerationError as exc:
            error_text = str(exc)
''' + TEXT_OLD + '''                text_reply_retry_count += 1
                if text_reply_retry_count <= MAX_TEXT_REPLY_RETRIES:
                    continue
            raise
        except Exception as exc:
            last_error = str(exc)
''' + TOKEN_OLD + '''                token = account_service.refresh_access_token(token, force=True, event="image_stream")
                continue
''' + TLS_OLD + '''                tls_retry_count += 1
                if tls_retry_count <= MAX_TLS_RETRIES:
                    continue
''' + CONNECTION_OLD + '''                conn_timeout_retry_count += 1
                if conn_timeout_retry_count <= MAX_CONN_TIMEOUT_RETRIES:
                    continue
            raise

def poll_existing_conversation(poll):
    for _ in range(3):
        if poll():
            return True
    return False
'''


class ImagePollTimeoutError(Exception):
    pass


class ImageContentPolicyError(Exception):
    pass


class ImageGenerationError(Exception):
    pass


class MockBackend:
    def __init__(self, access_token):
        self.progress_callback = None


class MockAccountService:
    def mark_image_result(self, token, ok):
        pass

    def refresh_access_token(self, token, force, event):
        return token


class MockLogger:
    def warning(self, event):
        pass


class DisableImagePollResubmitTests(unittest.TestCase):
    def test_poll_timeout_never_starts_another_image_request(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "upstream/services/protocol/conversation.py"
            target.parent.mkdir(parents=True)
            target.write_bytes(SOURCE.replace("\n", "\r\n").encode())
            original = target.read_bytes()
            result = patch(root)
            self.assertTrue(result["changed"])
            self.assertEqual((Path(result["backup"]) / "conversation.py").read_bytes(), original)
            patched = target.read_bytes()
            self.assertEqual(patched.count(b"\n"), patched.count(b"\r\n"))
            self.assertEqual(patch(root), {"changed": False})
            namespace = {"ImagePollTimeoutError": ImagePollTimeoutError, "ImageContentPolicyError": ImageContentPolicyError,
                         "ImageGenerationError": ImageGenerationError, "OpenAIBackendAPI": MockBackend,
                         "is_model_text_reply_instead_of_image": lambda text: "image reply" in text,
                         "is_tls_connection_error": lambda text: "reset" in text,
                         "is_connection_timeout_error": lambda text: "timed out" in text,
                         "is_token_invalid_error": lambda text: "token invalidated" in text}
            exec(compile(patched, str(target), "exec"), namespace)  # noqa: S102
            calls = []
            def stream(backend):
                calls.append("sent")
                backend.progress_callback("starting_generation")
                raise ImagePollTimeoutError("result unknown")
            request = type("Request", (), {"progress_callback": None})()
            with self.assertRaises(ImagePollTimeoutError):
                namespace["generate"](stream, MockAccountService(), MockLogger(), request)
            self.assertEqual(calls, ["sent"])
            for error in (ConnectionError("connection reset"), TimeoutError("read timed out"), ImageGenerationError("image reply"), RuntimeError("token invalidated")):
                calls.clear()
                def after_submit(backend, error=error):
                    calls.append("sent")
                    backend.progress_callback("starting_generation")
                    raise error
                with self.assertRaises(type(error)):
                    namespace["generate"](after_submit, MockAccountService(), MockLogger(), request)
                self.assertEqual(calls, ["sent"])
            for error in (ConnectionError("connection reset"), TimeoutError("read timed out"), ImageGenerationError("image reply"), RuntimeError("token invalidated")):
                calls.clear()
                def before_submit(backend, error=error):
                    calls.append("attempt")
                    if len(calls) == 1:
                        backend.progress_callback("uploading")
                        raise error
                    backend.progress_callback("starting_generation")
                    return "ok"
                self.assertEqual(namespace["generate"](before_submit, MockAccountService(), MockLogger(), request), "ok")
                self.assertEqual(calls, ["attempt", "attempt"])
            calls.clear()
            events = []
            request.progress_callback = events.append
            def codex(backend):
                calls.append("attempt")
                backend.progress_callback("uploading")
                raise ConnectionError("connection reset")
            with self.assertRaises(ConnectionError):
                namespace["generate"](codex, MockAccountService(), MockLogger(), request, codex_model=True)
            self.assertEqual(calls, ["attempt"])
            self.assertEqual(events, ["uploading"])
            polls = []
            self.assertTrue(namespace["poll_existing_conversation"](lambda: polls.append(1) or len(polls) == 2))
            self.assertEqual(len(polls), 2)
            self.assertIn(NEW, patched.decode().replace("\r\n", "\n"))

    def test_unexpected_source_is_not_changed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "upstream/services/protocol/conversation.py"
            target.parent.mkdir(parents=True)
            target.write_text(SOURCE.replace("MAX_POLL_TIMEOUT_RETRIES = 4", "MAX_POLL_TIMEOUT_RETRIES = 5"), encoding="utf-8")
            original = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "differs"):
                patch(root)
            self.assertEqual(target.read_bytes(), original)
            self.assertFalse((root / "backups").exists())
