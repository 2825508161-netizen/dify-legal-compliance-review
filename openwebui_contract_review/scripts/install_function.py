from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE = os.getenv("OPENWEBUI_URL", "http://127.0.0.1:3000").rstrip("/")
FUNCTION_ID = "dify_contract_review"
ROOT = Path(__file__).resolve().parents[1]


def request(path: str, *, token: str = "", data: dict | None = None, method: str = "GET"):
    body = None if data is None else json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status, json.loads(response.read().decode("utf-8") or "null")


def main() -> int:
    for _ in range(90):
        try:
            request("/health")
            break
        except Exception:
            time.sleep(2)
    else:
        print("Open WebUI 未能在规定时间内启动。", file=sys.stderr)
        return 1

    _, session = request(
        "/api/v1/auths/signin",
        data={"email": "admin@localhost", "password": "admin"},
        method="POST",
    )
    token = session["token"]
    content = (ROOT / "openwebui_functions" / "dify_contract_review.py").read_text(
        encoding="utf-8"
    )
    payload = {
        "id": FUNCTION_ID,
        "name": "AI 合同审核（Dify 测试版）",
        "content": content,
        "meta": {
            "description": "上传 PDF、DOCX 或 TXT 合同，选择审核要求并调用 Dify 测试工作流。"
        },
    }
    try:
        request(f"/api/v1/functions/id/{FUNCTION_ID}", token=token)
        _, current = request(
            f"/api/v1/functions/id/{FUNCTION_ID}/update",
            token=token,
            data=payload,
            method="POST",
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        _, current = request(
            "/api/v1/functions/create",
            token=token,
            data=payload,
            method="POST",
        )
    if not current.get("is_active"):
        _, current = request(
            f"/api/v1/functions/id/{FUNCTION_ID}/toggle",
            token=token,
            data={},
            method="POST",
        )
    if not current.get("is_active"):
        print("合同审核入口未能启用。", file=sys.stderr)
        return 1
    print("AI 合同审核入口已安装并启用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
