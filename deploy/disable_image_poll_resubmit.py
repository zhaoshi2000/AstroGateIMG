"""Stop new image requests after an upstream image-result polling timeout."""
import argparse
import ast
import os
import time
from pathlib import Path

SETTINGS = "    # 轮询超时错误最大重试次数（换账号重试）\n    MAX_POLL_TIMEOUT_RETRIES = 4\n"
COUNTER = "    poll_timeout_retry_count = 0\n"
OLD = '''        except ImagePollTimeoutError as exc:
            account_service.mark_image_result(token, False)
            if account_email:
                setattr(exc, "account_email", account_email)
            # 轮询超时：换账号重试
            if not emitted_for_token:
                poll_timeout_retry_count += 1
                if poll_timeout_retry_count <= MAX_POLL_TIMEOUT_RETRIES:
                    logger.warning({
                        "event": "image_poll_timeout_retry",
                        "request_token": token,
                        "account_email": account_email,
                        "retry_count": poll_timeout_retry_count,
                        "index": index,
                        "error": str(exc)[:200],
                    })
                    continue
                logger.warning({
                    "event": "image_poll_timeout_exhausted_retries",
                    "request_token": token,
                    "account_email": account_email,
                    "retry_count": poll_timeout_retry_count,
                    "index": index,
                })
                raise
            raise
'''
NEW = '''        except ImagePollTimeoutError as exc:
            account_service.mark_image_result(token, False)
            if account_email:
                setattr(exc, "account_email", account_email)
            raise
'''
BACKEND_OLD = '''        backend = None
        try:
            backend = OpenAIBackendAPI(access_token=token)
            if request.progress_callback:
                backend.progress_callback = request.progress_callback
'''
BACKEND_NEW = '''        backend = None
        submitted = codex_model
        try:
            backend = OpenAIBackendAPI(access_token=token)
            def track_image_progress(step):
                nonlocal submitted
                if step == "starting_generation":
                    submitted = True
                if request.progress_callback:
                    request.progress_callback(step)
            backend.progress_callback = track_image_progress
'''
TEXT_OLD = "            if is_model_text_reply_instead_of_image(error_text) and not emitted_for_token:\n"
TEXT_NEW = "            if not submitted and is_model_text_reply_instead_of_image(error_text) and not emitted_for_token:\n"
TLS_OLD = "            if not emitted_for_token and is_tls_connection_error(last_error):\n"
TLS_NEW = "            if not submitted and not emitted_for_token and is_tls_connection_error(last_error):\n"
CONNECTION_OLD = "            if not emitted_for_token and is_connection_timeout_error(last_error):\n"
CONNECTION_NEW = "            if not submitted and not emitted_for_token and is_connection_timeout_error(last_error):\n"
TOKEN_OLD = "            if not emitted_for_token and is_token_invalid_error(last_error):\n"
TOKEN_NEW = "            if not submitted and not emitted_for_token and is_token_invalid_error(last_error):\n"
CHANGES = ((SETTINGS, ""), (COUNTER, ""), (OLD, NEW), (BACKEND_OLD, BACKEND_NEW),
           (TEXT_OLD, TEXT_NEW), (TLS_OLD, TLS_NEW), (CONNECTION_OLD, CONNECTION_NEW), (TOKEN_OLD, TOKEN_NEW))


def patch(root):
    source_path = root / "upstream" / "services" / "protocol" / "conversation.py"
    original = source_path.read_bytes()
    text = original.decode("utf-8")
    crlf = "\r\n" in text
    source = text.replace("\r\n", "\n")
    if "\r" in source or (crlf and text.count("\r\n") != text.count("\n")):
        raise ValueError("Upstream source has inconsistent line endings; no changes made")
    if all(old not in source and (not new or source.count(new) == 1) for old, new in CHANGES):
        return {"changed": False}
    if any(source.count(old) != 1 for old, _ in CHANGES):
        raise ValueError("Upstream source differs from the expected image-poll retry path; no changes made")
    updated = source
    for old, new in CHANGES:
        updated = updated.replace(old, new, 1)
    ast.parse(updated)
    backup = root / "backups" / ("before-image-poll-resubmit-" + str(time.time_ns()))
    backup.mkdir(parents=True, mode=0o700)
    with os.fdopen(os.open(backup / "conversation.py", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        stream.write(original)
    source_path.write_bytes(updated.replace("\n", "\r\n").encode("utf-8") if crlf else updated.encode("utf-8"))
    return {"changed": True, "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/astrogate-img"))
    args = parser.parse_args()
    print(patch(args.root))
