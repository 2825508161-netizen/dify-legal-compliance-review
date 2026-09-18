import json
import re
from datetime import datetime, timezone


def _load(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except Exception:
        return {
            "analysis_status": "failed",
            "failure_reason": "结果无法解析",
            "risks": [],
        }


def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _display_location(location):
    location = location if isinstance(location, dict) else {}
    page_start = _as_int(location.get("page_start"))
    page_end = _as_int(location.get("page_end"))
    paragraph_start = _as_int(location.get("paragraph_start"))
    paragraph_end = _as_int(location.get("paragraph_end"))
    if paragraph_start is not None:
        paragraph_end = paragraph_end or paragraph_start
        if page_start is None:
            return (
                "第%s段" % paragraph_start
                if paragraph_start == paragraph_end
                else "第%s—%s段" % (paragraph_start, paragraph_end)
            )
        page_end = page_end or page_start
        if page_start == page_end:
            return (
                "第%s页·第%s段" % (page_start, paragraph_start)
                if paragraph_start == paragraph_end
                else "第%s页·第%s—%s段"
                % (page_start, paragraph_start, paragraph_end)
            )
        return "第%s页·第%s段—第%s页·第%s段" % (
            page_start,
            paragraph_start,
            page_end,
            paragraph_end,
        )

    # Backward compatibility for cached results produced before Chinese locators.
    legacy = str(location.get("locator_label") or "")
    match = re.fullmatch(r"P\?/¶(\d+)(?:-P\?/¶(\d+))?", legacy)
    if match:
        start, end = match.group(1), match.group(2) or match.group(1)
        return "第%s段" % start if start == end else "第%s—%s段" % (start, end)
    return legacy or "位置待核验"


def _dedupe_bucket(category):
    # These labels often describe the same contract defect from different
    # angles. Dedupe them by exact source excerpt, while keeping specialist
    # risks such as data/privacy or employment separate.
    general_contract = {
        "合同权利义务",
        "价款与税务",
        "期限与终止",
        "违约责任",
        "争议解决",
        "主体与授权",
    }
    return "合同通用" if category in general_contract else category


def main(iteration_results: list, declared_doc_type: str, jurisdiction: str) -> dict:
    risks = []
    traces = []
    unresolved = []
    warnings = []
    for raw in iteration_results or []:
        item = _load(raw)
        if item.get("analysis_status") != "ok":
            unresolved.append(
                {
                    "chunk_id": item.get("chunk_id", "UNKNOWN"),
                    "failure_reason": item.get("failure_reason", "未知错误"),
                }
            )
            continue
        risks.extend(item.get("risks") or [])
        traces.extend(item.get("retrieval_trace") or [])
        if item.get("analysis_warning"):
            warnings.append(
                {
                    "chunk_id": item.get("chunk_id", "UNKNOWN"),
                    "message": item.get("analysis_warning"),
                }
            )

    deduplicated = []
    seen = set()
    for risk in risks:
        category = (risk.get("risk_category") or "其他").strip()
        key = (
            _dedupe_bucket(category),
            (
                risk.get("original_excerpt")
                or risk.get("title")
                or ""
            ).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(risk)

    score = {"高": 3, "中": 2, "低": 1}
    deduplicated.sort(
        key=lambda risk: -score.get(risk.get("risk_level"), 0)
    )
    for index, risk in enumerate(deduplicated, start=1):
        risk["risk_id"] = "RISK-%03d" % index
        risk.setdefault("location", {})["locator_label"] = _display_location(
            risk.get("location")
        )

    counts = {
        level: sum(
            1 for risk in deduplicated if risk.get("risk_level") == level
        )
        for level in ("高", "中", "低")
    }
    manual_review_required = bool(
        unresolved
        or counts["高"]
        or any(risk.get("needs_manual_review") for risk in deduplicated)
    )
    summary = "共识别 %d 项风险：高 %d 项、中 %d 项、低 %d 项。" % (
        len(deduplicated),
        counts["高"],
        counts["中"],
        counts["低"],
    )
    if manual_review_required:
        summary += (
            " 高风险或依据不确定项必须由法务结合完整原文和最新有效法规复核。"
        )

    report = {
        "report_meta": {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "legal_disclaimer": "仅供内部辅助审查，不构成正式法律意见。",
        },
        "review_parameters": {
            "declared_doc_type": declared_doc_type,
            "jurisdiction": jurisdiction,
        },
        "risk_summary": {
            "counts": {
                "total": len(deduplicated),
                "high": counts["高"],
                "medium": counts["中"],
                "low": counts["低"],
            },
            "executive_summary": summary,
            "manual_review_required": manual_review_required,
        },
        "priority_remediation": [
            {
                "risk_id": risk.get("risk_id"),
                "level": risk.get("risk_level"),
                "title": risk.get("title"),
                "location": (risk.get("location") or {}).get(
                    "locator_label"
                ),
                "action": risk.get("recommendation"),
            }
            for risk in deduplicated
            if risk.get("risk_level") in ("高", "中")
        ][:10],
        "risks": deduplicated,
        "unresolved_chunks": unresolved,
        "processing_warnings": warnings,
        "retrieval_trace": traces[:100],
    }

    lines = [
        "# 文档法律与合规风险审查报告",
        "",
        "> " + report["report_meta"]["legal_disclaimer"],
        "",
        "## 执行摘要",
        "",
        summary,
        "",
        "## 重点整改清单",
        "",
    ]
    if report["priority_remediation"]:
        for item in report["priority_remediation"]:
            lines.append(
                "- **%s｜%s**（%s）：%s"
                % (
                    item["level"],
                    item["title"],
                    item["location"] or "位置待核验",
                    item["action"] or "交由法务复核",
                )
            )
    else:
        lines.append("- 未发现需列入重点整改清单的项目。")

    if warnings:
        lines.extend(["", "## 处理提示", ""])
        for warning in warnings:
            lines.append(
                "- %s：%s"
                % (warning.get("chunk_id"), warning.get("message"))
            )

    lines += ["", "## 风险明细", ""]
    for risk in deduplicated:
        location = (risk.get("location") or {}).get(
            "locator_label"
        ) or "位置待核验"
        lines += [
            "### %s｜%s｜%s"
            % (
                risk.get("risk_id"),
                risk.get("risk_level"),
                risk.get("title"),
            ),
            "",
            "- 类别：%s" % risk.get("risk_category", "其他"),
            "- 位置：%s" % location,
            "- 原文：> %s"
            % (risk.get("original_excerpt") or "未提供"),
            "- 问题：%s" % (risk.get("issue") or ""),
            "- 可能后果：%s"
            % (risk.get("possible_consequences") or ""),
            "- 修改建议：%s"
            % (risk.get("recommendation") or "交由法务复核"),
            "- 建议替换条款：%s"
            % (risk.get("replacement_clause") or "无"),
            "- 判断理由摘要：%s"
            % (risk.get("reasoning_summary") or ""),
            "- 人工复核：%s"
            % (
                "是"
                if risk.get("needs_manual_review")
                or risk.get("risk_level") == "高"
                else "否"
            ),
            "",
            "法律依据：",
        ]
        for base in risk.get("legal_bases") or []:
            article = (
                " " + str(base.get("article"))
                if base.get("article")
                else ""
            )
            lines.append(
                "- %s%s｜%s"
                % (
                    base.get("title") or "未确认依据",
                    article,
                    base.get("verification_status") or "需人工复核",
                )
            )
        lines.append("")

    if unresolved:
        lines += [
            "## 未完成分段",
            "",
            json.dumps(unresolved, ensure_ascii=False),
        ]

    return {
        "report_markdown": "\n".join(lines),
        "report_json": json.dumps(
            report, ensure_ascii=False, indent=2
        ),
        "high_risk_count": counts["高"],
        "manual_review_required": manual_review_required,
    }
