from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class DifyIntegrationError(RuntimeError):
    """An error safe to show to a local end user."""


@dataclass(frozen=True)
class ReviewOptions:
    declared_doc_type: str
    industry: str
    jurisdiction: str
    strictness: str
    focus_risk_types: str


def _safe_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是", "需要"}
    return False


def _output_files(value: Any, hint: str = "") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        if isinstance(value.get("url"), str):
            found.append((hint, value))
        for key, item in value.items():
            found.extend(_output_files(item, str(key)))
    elif isinstance(value, list):
        for item in value:
            found.extend(_output_files(item, hint))
    return found


def _extension_for(hint: str, item: dict[str, Any]) -> str | None:
    name = str(item.get("name") or item.get("filename") or "")
    suffix = Path(name).suffix.lower()
    if suffix in {".docx", ".pdf"}:
        return suffix
    mime = str(item.get("mime_type") or item.get("mime") or "").lower()
    if "wordprocessingml" in mime:
        return ".docx"
    if "pdf" in mime:
        return ".pdf"
    lowered = hint.lower()
    if "word" in lowered or "docx" in lowered:
        return ".docx"
    if "pdf" in lowered:
        return ".pdf"
    return None


class DifyClient:
    def __init__(self) -> None:
        self.mode = os.getenv("DIFY_MODE", "mock").strip().lower()
        self.api_base = os.getenv("DIFY_API_BASE", "https://api.dify.ai/v1").rstrip("/")
        self.api_key = os.getenv("DIFY_API_KEY", "").strip()
        self.timeout = float(os.getenv("DIFY_TIMEOUT_SECONDS", "300"))
        configured_hosts = os.getenv(
            "DIFY_ALLOWED_FILE_HOSTS", "upload.dify.ai,cloud.dify.ai"
        )
        self.allowed_file_hosts = {
            host.strip().lower() for host in configured_hosts.split(",") if host.strip()
        }

    @property
    def live_ready(self) -> bool:
        return self.mode == "live" and bool(self.api_key)

    async def run_review(
        self,
        *,
        source_path: Path,
        original_name: str,
        content_type: str,
        options: ReviewOptions,
        local_user_id: str,
        report_dir: Path,
    ) -> dict[str, Any]:
        if self.mode == "mock":
            return await self._mock_review(report_dir)
        if not self.api_key:
            raise DifyIntegrationError(
                "尚未配置 Dify 测试应用密钥。请完成本地安全配置后重试。"
            )

        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = httpx.Timeout(self.timeout, connect=20.0)
        anonymous_user = "openwebui-" + hashlib.sha256(
            local_user_id.encode("utf-8")
        ).hexdigest()[:16]

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                with source_path.open("rb") as handle:
                    upload = await client.post(
                        f"{self.api_base}/files/upload",
                        headers=headers,
                        data={"user": anonymous_user},
                        files={
                            "file": (
                                original_name,
                                handle,
                                content_type or "application/octet-stream",
                            )
                        },
                    )
                self._raise_for_dify(upload, "上传合同")
                upload_data = upload.json()
                upload_id = upload_data.get("id")
                if not upload_id:
                    raise DifyIntegrationError("Dify 未返回文件编号，无法开始审核。")

                workflow = await client.post(
                    f"{self.api_base}/workflows/run",
                    headers={**headers, "Content-Type": "application/json"},
                    json={
                        "inputs": {
                            "document": {
                                "transfer_method": "local_file",
                                "upload_file_id": upload_id,
                                "type": "document",
                            },
                            "declared_doc_type": options.declared_doc_type,
                            "industry": options.industry,
                            "jurisdiction": options.jurisdiction,
                            "strictness": options.strictness,
                            "focus_risk_types": options.focus_risk_types,
                        },
                        "response_mode": "blocking",
                        "user": anonymous_user,
                    },
                )
                self._raise_for_dify(workflow, "执行合同审核")
                payload = workflow.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict):
                    raise DifyIntegrationError("Dify 返回格式异常，未找到审核结果。")
                if str(data.get("status", "")).lower() == "failed":
                    raise DifyIntegrationError(
                        "Dify 工作流运行失败：" + str(data.get("error") or "未知原因")[:300]
                    )
                outputs = data.get("outputs")
                if not isinstance(outputs, dict):
                    raise DifyIntegrationError("Dify 已响应，但没有返回报告输出。")

                downloads = await self._download_outputs(client, outputs, report_dir)
        except httpx.TimeoutException as exc:
            raise DifyIntegrationError(
                "Dify 审核超时。合同可能过长，请稍后重试或减少文档内容。"
            ) from exc
        except httpx.NetworkError as exc:
            raise DifyIntegrationError(
                "无法连接 Dify 服务，请检查网络后重试。"
            ) from exc
        except json.JSONDecodeError as exc:
            raise DifyIntegrationError("Dify 返回了无法解析的响应。") from exc

        result = self._normalise_outputs(outputs, downloads)
        result["integration"] = {
            "mode": "live",
            "workflow_run_id": str(
                payload.get("workflow_run_id") or data.get("id") or ""
            ),
            "task_id": str(payload.get("task_id") or ""),
            "status": str(data.get("status") or "succeeded"),
            "elapsed_time": data.get("elapsed_time"),
            "total_tokens": data.get("total_tokens"),
            "created_at": data.get("created_at"),
            "finished_at": data.get("finished_at"),
        }
        return result

    def _raise_for_dify(self, response: httpx.Response, action: str) -> None:
        if 200 <= response.status_code < 300:
            return
        if response.status_code in {401, 403}:
            message = "Dify 密钥无效或无权访问测试应用。"
        elif response.status_code == 429:
            message = "Dify 当前请求过多或模型额度不足，请稍后重试。"
        elif response.status_code >= 500:
            message = "Dify 服务暂时异常，请稍后重试。"
        else:
            try:
                detail = response.json().get("message") or response.json().get("error")
            except Exception:
                detail = None
            message = str(detail or f"HTTP {response.status_code}")[:300]
        raise DifyIntegrationError(f"{action}失败：{message}")

    def _normalise_outputs(
        self, outputs: dict[str, Any], downloads: dict[str, str]
    ) -> dict[str, Any]:
        markdown = str(outputs.get("report_markdown") or "").strip()
        report_json = _safe_json(outputs.get("report_json") or {})
        try:
            high_risk_count = int(outputs.get("high_risk_count") or 0)
        except (TypeError, ValueError):
            high_risk_count = 0
        manual_review = _safe_bool(outputs.get("manual_review_required"))
        if not markdown:
            markdown = "# 合同审核完成\n\nDify 已完成运行，但没有返回 Markdown 报告，请人工检查工作流输出。"
        return {
            "report_markdown": markdown,
            "report_json": report_json,
            "high_risk_count": max(0, high_risk_count),
            "manual_review_required": manual_review,
            "downloads": downloads,
        }

    async def _download_outputs(
        self,
        client: httpx.AsyncClient,
        outputs: dict[str, Any],
        report_dir: Path,
    ) -> dict[str, str]:
        downloads: dict[str, str] = {}
        for hint, item in _output_files(outputs):
            extension = _extension_for(hint, item)
            if not extension or extension.lstrip(".") in downloads:
                continue
            url = str(item.get("url") or "")
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in self.allowed_file_hosts:
                raise DifyIntegrationError("Dify 返回了不受信任的报告下载地址。")
            response = await client.get(url, follow_redirects=False)
            if 300 <= response.status_code < 400:
                redirect = response.headers.get("location", "")
                redirected = urlparse(redirect)
                if redirected.scheme != "https" or redirected.hostname not in self.allowed_file_hosts:
                    raise DifyIntegrationError("报告下载跳转到了不受信任的地址。")
                response = await client.get(redirect, follow_redirects=False)
            if response.status_code != 200:
                raise DifyIntegrationError(
                    f"Dify 审核成功，但{extension}报告下载失败。"
                )
            target = report_dir / f"contract-review{extension}"
            target.write_bytes(response.content)
            downloads[extension.lstrip(".")] = target.name
        return downloads

    async def _mock_review(self, report_dir: Path) -> dict[str, Any]:
        docx = Path(os.getenv("MOCK_DOCX_PATH", ""))
        pdf = Path(os.getenv("MOCK_PDF_PATH", ""))
        downloads: dict[str, str] = {}
        for kind, source in (("docx", docx), ("pdf", pdf)):
            if source.is_file():
                target = report_dir / f"contract-review.{kind}"
                shutil.copyfile(source, target)
                downloads[kind] = target.name
        return {
            "report_markdown": (
                "# 合同法律与合规风险审查报告（模拟联调）\n\n"
                "> 当前为本地模拟模式，用于验证上传、进度、展示和下载。"
                "这不是一次真实 Dify 审核。\n\n"
                "## 执行摘要\n\n"
                "发现 1 项高风险示例。所有高风险事项均须由法务人工复核。\n\n"
                "## 风险明细\n\n"
                "### RISK-001｜高｜单方免责范围过宽\n\n"
                "- 位置：页码待定位·第3段\n"
                "- 法律依据：需人工复核\n"
                "- 建议：明确免责边界，并排除故意或重大过失情形。"
            ),
            "report_json": {
                "mode": "mock",
                "risk_summary": {"high": 1, "medium": 0, "low": 0},
                "manual_review_required": True,
            },
            "high_risk_count": 1,
            "manual_review_required": True,
            "downloads": downloads,
            "integration": {
                "mode": "mock",
                "workflow_run_id": "",
                "task_id": "",
                "status": "simulated",
            },
        }
