from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.dify_client import (
    DifyClient,
    DifyIntegrationError,
    ReviewOptions,
    _extension_for,
    _output_files,
    _safe_bool,
    _safe_json,
)
from app import main


def test_safe_json_parses_object() -> None:
    assert _safe_json('{"high": 2}') == {"high": 2}


def test_safe_json_keeps_plain_text() -> None:
    assert _safe_json("需人工复核") == "需人工复核"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", True), ("false", False), ("是", True), (None, False)],
)
def test_safe_bool(value: object, expected: bool) -> None:
    assert _safe_bool(value) is expected


def test_output_files_finds_nested_word_and_pdf() -> None:
    outputs = {
        "word_file": [{"url": "https://upload.dify.ai/a", "name": "r.docx"}],
        "pdf_file": {"files": [{"url": "https://upload.dify.ai/b", "mime_type": "application/pdf"}]},
    }
    found = _output_files(outputs)
    assert len(found) == 2
    assert {_extension_for(hint, item) for hint, item in found} == {".docx", ".pdf"}


def test_extension_uses_output_hint_when_name_missing() -> None:
    assert _extension_for("word_file", {"url": "https://upload.dify.ai/a"}) == ".docx"
    assert _extension_for("pdf_file", {"url": "https://upload.dify.ai/b"}) == ".pdf"


def test_review_options_match_dify_start_variables() -> None:
    options = ReviewOptions("采购合同", "制造业", "中国大陆", "严格", "数据隐私")
    assert options.declared_doc_type == "采购合同"
    assert options.focus_risk_types == "数据隐私"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "密钥无效"),
        (403, "密钥无效"),
        (429, "请求过多"),
        (500, "服务暂时异常"),
        (400, "参数错误"),
    ],
)
def test_dify_http_errors_are_clear(status: int, expected: str) -> None:
    request = httpx.Request("POST", "https://api.dify.ai/v1/workflows/run")
    response = httpx.Response(status, request=request, json={"message": "参数错误"})
    with pytest.raises(DifyIntegrationError, match=expected):
        DifyClient()._raise_for_dify(response, "执行合同审核")


def test_normalise_outputs_handles_bad_counts_and_false_string() -> None:
    result = DifyClient()._normalise_outputs(
        {
            "report_markdown": "完成",
            "report_json": '{"ok": true}',
            "high_risk_count": "不是数字",
            "manual_review_required": "false",
        },
        {},
    )
    assert result["high_risk_count"] == 0
    assert result["manual_review_required"] is False
    assert result["report_json"] == {"ok": True}


def test_normalise_outputs_clamps_negative_high_risk_count() -> None:
    result = DifyClient()._normalise_outputs(
        {"high_risk_count": -8, "manual_review_required": "是"}, {}
    )
    assert result["high_risk_count"] == 0
    assert result["manual_review_required"] is True
    assert "没有返回 Markdown" in result["report_markdown"]


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(main, "REPORT_DIR", tmp_path)
    main.jobs.clear()
    main.tasks.clear()
    monkeypatch.setenv("DIFY_MODE", "mock")
    monkeypatch.setenv("MOCK_DOCX_PATH", "")
    monkeypatch.setenv("MOCK_PDF_PATH", "")
    with TestClient(main.app) as client:
        yield client
    main.jobs.clear()
    main.tasks.clear()


def test_health_reports_mock_not_configured(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "mock",
        "dify_configured": False,
        "result_cache_enabled": False,
        "version": "1.1.0",
    }


def test_review_endpoint_rejects_unsupported_file(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/reviews", files={"file": ("contract.md", b"test", "text/markdown")}
    )
    assert response.status_code == 400
    assert "仅支持" in response.json()["detail"]


def test_review_endpoint_rejects_empty_file(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/reviews", files={"file": ("contract.txt", b"", "text/plain")}
    )
    assert response.status_code == 400
    assert "文件为空" in response.json()["detail"]


def test_review_endpoint_rejects_oversized_file(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "MAX_FILE_BYTES", 4)
    response = api_client.post(
        "/api/reviews", files={"file": ("contract.txt", b"12345", "text/plain")}
    )
    assert response.status_code == 413
    assert "30 MB" in response.json()["detail"]


def test_mock_review_completes_and_preserves_location_rule(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/reviews",
        files={"file": ("contract.txt", "模拟合同".encode(), "text/plain")},
        data={
            "declared_doc_type": "采购合同",
            "industry": "制造业",
            "jurisdiction": "中国大陆",
            "strictness": "严格",
            "focus_risk_types": "违约责任、数据与隐私",
        },
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    result = api_client.get(f"/api/reviews/{job_id}")
    assert result.status_code == 200
    payload = result.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "mock"
    assert payload["result"]["high_risk_count"] == 1
    assert payload["result"]["integration"]["mode"] == "mock"
    assert payload["result"]["integration"]["workflow_run_id"] == ""
    assert "页码待定位" in payload["result"]["report_markdown"]


def test_review_lookup_and_download_block_path_traversal(api_client: TestClient) -> None:
    assert api_client.get("/api/reviews/not-a-job").status_code == 404
    assert api_client.get("/api/reports/not-a-job/contract-review.pdf").status_code == 404


def test_no_secret_is_hard_coded() -> None:
    source = Path("app/dify_client.py").read_text(encoding="utf-8")
    assert "app-" not in source
    assert 'os.getenv("DIFY_API_KEY", "")' in source
