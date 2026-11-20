"""Publish a checked concept to its own branch of one Cloudflare Pages project."""

import hashlib
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from storage import ROOT, runtime_dir

load_dotenv(ROOT / ".env")
_project_lock = threading.Lock()


def project_name():
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    suffix = hashlib.sha256(account.encode()).hexdigest()[:8]
    return os.environ.get("SITESCOUT_PAGES_PROJECT") or f"sitescout-previews-{suffix}"


def ensure_project():
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not re.fullmatch("[a-fA-F0-9]{32}", account) or not token:
        raise RuntimeError("Cloudflare Pages credentials are missing.")
    name = project_name()
    if not re.fullmatch("[a-z0-9][a-z0-9-]{0,56}", name):
        raise ValueError("Use a short lowercase Pages project name.")
    api = f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects"
    headers = {"Authorization": f"Bearer {token}"}
    with _project_lock:
        response = requests.get(f"{api}/{name}", headers=headers, timeout=15)
        if response.status_code == 404:
            response = requests.post(
                api, headers=headers, json={"name": name, "production_branch": "main"}, timeout=20
            )
        if not response.ok:
            raise RuntimeError(f"Cloudflare Pages project lookup failed (HTTP {response.status_code}).")
        result = response.json()["result"]
        return result["subdomain"]


def preview_address(run_id, lead_id, domain):
    alias = "p-" + hashlib.sha256(f"{run_id}/{lead_id}".encode()).hexdigest()[:18]
    return alias, f"https://{alias}.{domain}"


def publish(preview, alias):
    if not re.fullmatch(r"p-[0-9a-f]{18}", alias):
        raise ValueError("Invalid preview ID.")
    folder = runtime_dir() / "previews" / alias
    folder.mkdir(parents=True, exist_ok=True)
    marker = hashlib.sha256(preview["html"].encode()).hexdigest()[:20]
    (folder / "index.html").write_text(preview["html"] + f"\n<!-- sitescout-{marker} -->")
    (folder / "_headers").write_text(
        "/*\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: no-referrer\n  Content-Security-Policy: form-action 'none'; base-uri 'none'\n"
    )
    wrapper = ROOT / "node_modules/wrangler/bin/wrangler.js"
    if not wrapper.exists():
        raise RuntimeError("Run npm ci to install the preview uploader.")
    # A direct upload contains only this single preview folder. Credentials,
    # local traces and the operator dashboard are outside the upload tree.
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "TMPDIR", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        if k in os.environ
    }
    env.update({"CI": "true", "WRANGLER_SEND_METRICS": "false"})
    command = [
        "node",
        str(wrapper),
        "pages",
        "deploy",
        str(folder),
        "--project-name",
        project_name(),
        "--branch",
        alias,
        "--commit-dirty=true",
    ]
    result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=110)
    if result.returncode:
        safe_error = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr or result.stdout)
        for secret in (env.get("CLOUDFLARE_API_TOKEN"), env.get("CLOUDFLARE_ACCOUNT_ID")):
            if secret:
                safe_error = safe_error.replace(secret, "[redacted]")
        raise RuntimeError("Cloudflare upload failed: " + safe_error.strip()[-650:])
    url = preview["url"]
    for pause in (0, 1, 2, 4, 6, 9, 12):
        time.sleep(pause)
        try:
            response = requests.get(url, timeout=7)
            if response.ok and f"sitescout-{marker}" in response.text:
                return {
                    "published": True,
                    "http_status": response.status_code,
                    "content_verified": True,
                }
        except requests.RequestException:
            pass
    raise RuntimeError("Upload returned, but the hosted preview could not yet be verified.")
