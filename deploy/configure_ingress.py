"""Add the image subdomain to this server's existing HAProxy + nginx ingress.

Run as root after copying the repository to /opt/astrogate-img.
Only the img.astrogate.sbs route and its nginx site are added.
"""
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone

DOMAIN = "img.astrogate.sbs"
stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
haproxy = Path("/etc/haproxy/haproxy.cfg")
original = haproxy.read_text()
configuration = original
if "backend astrogate_img_http" not in configuration:
    configuration = configuration.replace(
        "frontend http_in\n    bind 0.0.0.0:80\n",
        "frontend http_in\n    mode http\n    bind 0.0.0.0:80\n"
        "    acl astrogate_img_host hdr(host),lower,field(1,:) -m str img.astrogate.sbs\n"
        "    use_backend astrogate_img_http if astrogate_img_host\n",
    )
    configuration = configuration.replace("backend astrogate_http\n", "backend astrogate_http\n    mode http\n")
    configuration = configuration.replace("    default_backend xray\n", "    use_backend astrogate_img_tls if { req.ssl_sni -i img.astrogate.sbs }\n    default_backend xray\n")
    configuration += "\nbackend astrogate_img_http\n    mode http\n    server local_img_http 127.0.0.1:9464\n\nbackend astrogate_img_tls\n    server local_img_tls 127.0.0.1:9465 send-proxy\n"
    if "acl astrogate_img_host" not in configuration or "use_backend astrogate_img_tls" not in configuration:
        raise RuntimeError("The existing HAProxy structure has changed; no config was written")

webroot = Path("/var/www/astrogate-img")
(webroot / ".well-known/acme-challenge").mkdir(parents=True, exist_ok=True)
certificate = Path(f"/etc/letsencrypt/live/{DOMAIN}/fullchain.pem")
site = f"""server {{
    listen 127.0.0.1:9464;
    server_name {DOMAIN};
    location ^~ /.well-known/acme-challenge/ {{ root {webroot}; }}
    location / {{ {'return 301 https://$host$request_uri;' if certificate.exists() else 'return 503;'} }}
}}
"""
if certificate.exists():
    site += f"""
server {{
    listen 127.0.0.1:9465 ssl http2 proxy_protocol;
    server_name {DOMAIN};
    ssl_certificate {certificate};
    ssl_certificate_key /etc/letsencrypt/live/{DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    set_real_ip_from 127.0.0.1;
    real_ip_header proxy_protocol;
    client_max_body_size 75m;
    access_log /var/log/nginx/astrogate-img.access.log;
    error_log /var/log/nginx/astrogate-img.error.log;
    add_header Strict-Transport-Security "max-age=31536000" always;
    location / {{
        proxy_pass http://127.0.0.1:43921;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
    }}
}}
"""
available = Path(f"/etc/nginx/sites-available/{DOMAIN}")
enabled = Path(f"/etc/nginx/sites-enabled/{DOMAIN}")
shutil.copy2(haproxy, haproxy.with_name(f"haproxy.cfg.before-img-{stamp}"))
prior_site = available.read_bytes() if available.exists() else None
if prior_site is not None:
    shutil.copy2(available, available.with_name(f"{DOMAIN}.before-{stamp}"))
try:
    haproxy.write_text(configuration)
    available.write_text(site)
    if not enabled.exists():
        enabled.symlink_to(available)
    subprocess.run(["haproxy", "-c", "-f", str(haproxy)], check=True)
    subprocess.run(["nginx", "-t"], check=True)
except BaseException:
    haproxy.write_text(original)
    if prior_site is None:
        enabled.unlink(missing_ok=True)
        available.unlink(missing_ok=True)
    else:
        available.write_bytes(prior_site)
    raise
subprocess.run(["systemctl", "reload", "nginx"], check=True)
subprocess.run(["systemctl", "reload", "haproxy"], check=True)
print("Image ingress configured; HTTPS enabled:", certificate.exists())
