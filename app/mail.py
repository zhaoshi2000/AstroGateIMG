"""Transactional registration messages through the operator's SMTP server."""
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from datetime import datetime, timezone
from html import escape
import smtplib
import ssl


def send_registration_code(settings, recipient: str, code: str) -> None:
    message = EmailMessage()
    message["Subject"] = "AstroGate IMG 邮箱注册验证码"
    message["From"] = formataddr(("AstroGate IMG", settings.smtp_sender))
    message["To"] = recipient
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = make_msgid(domain=settings.smtp_sender.rsplit("@", 1)[-1])
    message.set_content(
        f"欢迎注册 AstroGate IMG。\n\n你的邮箱验证码是：{code}\n验证码 10 分钟内有效，请勿向他人透露。\n\n"
        "如果不是你本人发起的注册，请忽略这封邮件。\n\nAstroGate IMG\nhttps://img.astrogate.sbs\n"
    )
    message.add_alternative(
        '<!doctype html><html lang="zh-CN"><body style="margin:0;background:#f6f3fc;font-family:Arial,sans-serif;color:#282231">'
        '<div style="max-width:480px;margin:40px auto;background:white;border:1px solid #e9e2f5;border-radius:16px;padding:35px">'
        '<p style="color:#7048ec;font-weight:bold;font-size:18px">AstroGate IMG</p>'
        '<h1 style="font-size:24px">验证你的邮箱</h1>'
        f'<p style="color:#8b8098;font-size:14px;line-height:1.8">你正在使用 {escape(recipient)} 注册，验证码为：</p>'
        f'<p style="background:#f4efff;border-radius:10px;text-align:center;padding:20px;font-size:36px;font-weight:bold;letter-spacing:9px;color:#7048ec">{code}</p>'
        '<p style="font-size:13px;color:#95899f;line-height:1.8">验证码 10 分钟内有效，请勿向他人透露。<br>如果不是你本人操作，请忽略此邮件。</p>'
        '<hr style="border:0;border-top:1px solid #eee8f5;margin:25px 0">'
        '<a href="https://img.astrogate.sbs" style="font-size:12px;color:#8d77b5">img.astrogate.sbs</a>'
        '</div></body></html>', subtype="html",
    )
    if settings.smtp_security not in {"none", "starttls", "ssl"}:
        raise ValueError("Unsupported SMTP security mode")
    smtp_class = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
    kwargs = {"host": settings.smtp_host, "port": settings.smtp_port, "timeout": 15}
    if settings.smtp_security == "ssl":
        kwargs["context"] = ssl.create_default_context()
    with smtp_class(**kwargs) as smtp:
        if settings.smtp_security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
