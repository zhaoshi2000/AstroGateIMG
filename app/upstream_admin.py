"""Public account projections for the authenticated operations console."""
import base64
import hashlib
import json


def jwt_claims(token):
    try:
        body = str(token).split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError, UnicodeError):
        return {}


def positive_int(value):
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError, OverflowError):
        return 0


def project_accounts(records):
    groups = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        claims = jwt_claims(record.get("access_token", ""))
        auth = claims.get("https://api.openai.com/auth") or {}
        if not isinstance(auth, dict):
            auth = {}
        email = str(record.get("email") or "")
        account_id = str(record.get("account_id") or auth.get("chatgpt_account_id") or "")
        owner = email.lower() or str(record.get("user_id") or auth.get("chatgpt_user_id") or claims.get("sub") or "")
        identity = account_id + ":" + owner if owner else str(record.get("access_token") or "unknown")
        key = hashlib.sha256(identity.encode()).hexdigest()[:20]
        group = groups.setdefault(key, [])
        group.append((positive_int(claims.get("iat")), record, auth))
    items = []
    tokens = {}
    for key, group in groups.items():
        _, record, auth = max(group, key=lambda item: item[0])
        tokens[key] = [row.get("access_token") for _, row, _ in group if row.get("access_token")]
        status = record.get("status") if record.get("status") in {"正常", "限流", "异常", "禁用"} else "未知"
        items.append({"id": key, "email": str(record.get("email") or "未提供邮箱"),
                      "plan_type": str(auth.get("chatgpt_plan_type") or record.get("type") or "未知"),
                      "status": status, "image_quota": positive_int(record.get("quota")),
                      "restore_at": record.get("restore_at"), "last_used_at": record.get("last_used_at"),
                      "success": positive_int(record.get("success")), "fail": positive_int(record.get("fail")),
                      "image_inflight": positive_int(record.get("image_inflight")), "credential_records": len(group)})
    stats = {"accounts": len(items), "records": sum(len(group) for group in groups.values()),
             "normal": sum(item["status"] == "正常" for item in items),
             "limited": sum(item["status"] == "限流" for item in items),
             "abnormal": sum(item["status"] in {"异常", "未知"} for item in items),
             "disabled": sum(item["status"] == "禁用" for item in items),
             "image_quota": sum(item["image_quota"] for item in items if item["status"] == "正常"),
             "image_inflight": sum(item["image_inflight"] for item in items)}
    return {"items": items, "stats": stats}, tokens
