import json
import re
from datetime import datetime, timezone


LEVEL_SCORE = {"低": 1, "中": 2, "高": 3}


def _loads(value, default):
    try:
        return json.loads(value)
    except Exception:
        return default


def _norm(value):
    return re.sub(r"[\s，。；：、,.!?！？:;（）()【】\[\]\"'“”‘’]+", "", str(value or "")).lower()


def _char_set(value):
    return set(_norm(value))


def _similar(a, b):
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    sa, sb = _char_set(a), _char_set(b)
    if not sa or not sb:
        return False
    return len(sa & sb) / max(1, len(sa | sb)) >= 0.72


def _merge_unique(items, key_func):
    result = []
    seen = set()
    for item in items:
        key = key_func(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _merge_risk(target, incoming):
    if LEVEL_SCORE.get(incoming.get("risk_level"), 0) > LEVEL_SCORE.get(
        target.get("risk_level"), 0
    ):
        target["risk_level"] = incoming["risk_level"]
    target["needs_manual_review"] = bool(
        target.get("needs_manual_review") or incoming.get("needs_manual_review")
    )
    target["confidence"] = max(
        float(target.get("confidence") or 0),
        float(incoming.get("confidence") or 0),
    )
    target["legal_bases"] = _merge_unique(
        (target.get("legal_bases") or []) + (incoming.get("legal_bases") or []),
        lambda item: (
            _norm(item.get("title")),
            _norm(item.get("article")),
            _norm(item.get("source_quote"))[:80],
        ),
    )
    target.setdefault("duplicate_locations", []).append(incoming.get("location") or {})


def _render_markdown(report):
    counts = report["risk_summary"]["counts"]
    lines = [
        "# 文档法律与合规风险审查报告",
        "",
        "> 本报告由自动化系统辅助生成，不构成正式法律意见。所有高风险项及标记“需人工复核”的依据，必须交由法务人员确认。",
        "",
        "## 一、执行摘要",
        "",
        "- 审查时间（UTC）：%s" % report["report_meta"]["reviewed_at"],
        "- 文档类型：%s" % report["document_profile"].get("identified_doc_type", "未确认"),
        "- 适用法域：%s" % report["review_parameters"]["jurisdiction"],
        "- 审查严格程度：%s" % report["review_parameters"]["strictness"],
        "- 风险总数：%s（高 %s / 中 %s / 低 %s）"
        % (
            counts["total"],
            counts["high"],
            counts["medium"],
            counts["low"],
        ),
        "- 高风险人工复核：%s"
        % ("是" if report["risk_summary"]["high_risk_requires_legal_review"] else "否"),
        "- 定位质量：%s" % report["report_meta"]["locator_quality"],
        "- 预处理提示：%s" % report["report_meta"]["preprocess_notice"],
        "",
        report["risk_summary"]["executive_summary"],
        "",
        "## 二、文档识别与关键要素",
        "",
        "```json",
        json.dumps(report["document_profile"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 三、重点整改清单",
        "",
    ]
    actions = report["priority_remediation"]
    if actions:
        for index, item in enumerate(actions, start=1):
            lines.append(
                "%s. **[%s] %s**：%s（位置：%s）"
                % (
                    index,
                    item["risk_level"],
                    item["title"],
                    item["recommendation"],
                    item["location"],
                )
            )
    else:
        lines.append("- 未识别出明确风险；仍建议人工抽查关键条款和知识库覆盖范围。")

    lines.extend(["", "## 四、风险明细", ""])
    if not report["risks"]:
        lines.append("未识别出明确风险。")
    for risk in report["risks"]:
        lines.extend(
            [
                "### %s [%s] %s"
                % (risk["risk_id"], risk["risk_level"], risk["title"]),
                "",
                "- 风险类别：%s" % risk["risk_category"],
                "- 原文位置：%s"
                % (risk.get("location", {}).get("locator_label") or "未定位"),
                "- 原文摘录：> %s"
                % (risk.get("original_excerpt") or "未提供"),
                "- 问题说明：%s" % (risk.get("issue") or "未提供"),
                "- 可能后果：%s"
                % (risk.get("possible_consequences") or "未提供"),
                "- 修改建议：%s" % (risk.get("recommendation") or "未提供"),
                "- 建议替换条款：%s"
                % (risk.get("replacement_clause") or "不适用/需法务起草"),
                "- 判断理由摘要：%s"
                % (risk.get("reasoning_summary") or "未提供"),
                "- 人工复核：%s%s"
                % (
                    "是" if risk.get("needs_manual_review") else "否",
                    (
                        "（%s）" % risk.get("uncertainty_reason")
                        if risk.get("uncertainty_reason")
                        else ""
                    ),
                ),
                "",
                "法律/制度依据：",
                "",
            ]
        )
        for base in risk.get("legal_bases") or []:
            lines.append(
                "- %s %s｜状态：%s｜依据摘录：%s"
                % (
                    base.get("title") or "未确认依据",
                    base.get("article") or "",
                    base.get("verification_status") or "需人工复核",
                    base.get("source_quote") or "无",
                )
            )
        lines.append("")

    lines.extend(
        [
            "## 五、未完成或需复核的分段",
            "",
        ]
    )
    if report["unresolved_chunks"]:
        for item in report["unresolved_chunks"]:
            lines.append(
                "- %s：%s" % (item["chunk_id"], item["failure_reason"])
            )
    else:
        lines.append("- 无。")

    lines.extend(
        [
            "",
            "## 六、可追溯性说明",
            "",
            "- 每个风险保留分段编号、页码（如解析器可提供）、段落范围、原文摘录、检索资料和判断理由摘要。",
            "- `retrieved_and_text_matched` 仅表示该依据在本次知识库检索文本中通过字面匹配，不代表法律适用结论已由律师确认。",
            "- 页码为 `P?` 时，说明 Dify 原生提取未保留可靠分页；段落编号仍可用于回查。",
            "",
        ]
    )
    return "\n".join(lines)


def main(
    iteration_results: list,
    profile_json: str,
    declared_doc_type: str,
    industry: str,
    jurisdiction: str,
    strictness: str,
    focus_risk_types: str,
    locator_quality: str,
    preprocess_notice: str,
    document_char_count: int,
    chunk_count: int,
) -> dict:
    profile = _loads(profile_json, {})
    all_risks = []
    traces = []
    elements = {}
    unresolved = []

    for raw in iteration_results or []:
        item = _loads(raw, {})
        if item.get("analysis_status") != "ok":
            unresolved.append(
                {
                    "chunk_id": item.get("chunk_id", "UNKNOWN"),
                    "failure_reason": item.get("failure_reason", "未知错误"),
                }
            )
            continue
        all_risks.extend(item.get("risks") or [])
        traces.extend(item.get("retrieval_trace") or [])
        for key, value in (item.get("extracted_elements") or {}).items():
            if value in (None, "", [], {}):
                continue
            elements.setdefault(key, [])
            values = value if isinstance(value, list) else [value]
            elements[key].extend(values)

    deduped = []
    for risk in all_risks:
        match = None
        for existing in deduped:
            if risk.get("risk_category") != existing.get("risk_category"):
                continue
            if _similar(
                risk.get("original_excerpt"), existing.get("original_excerpt")
            ) or _similar(risk.get("title"), existing.get("title")):
                match = existing
                break
        if match:
            _merge_risk(match, risk)
        else:
            risk["duplicate_locations"] = []
            deduped.append(risk)

    deduped.sort(
        key=lambda item: (
            -LEVEL_SCORE.get(item.get("risk_level"), 0),
            item.get("location", {}).get("paragraph_start") or 999999,
        )
    )
    for index, risk in enumerate(deduped, start=1):
        risk["risk_id"] = "RISK-%03d" % index

    for key, values in elements.items():
        elements[key] = _merge_unique(values, lambda value: _norm(value))[:50]

    if isinstance(profile, dict):
        profile["aggregated_elements"] = elements
    else:
        profile = {"raw_profile": profile, "aggregated_elements": elements}
    profile.setdefault("identified_doc_type", declared_doc_type)

    high = sum(1 for item in deduped if item.get("risk_level") == "高")
    medium = sum(1 for item in deduped if item.get("risk_level") == "中")
    low = sum(1 for item in deduped if item.get("risk_level") == "低")
    total = len(deduped)
    summary = (
        "系统共识别 %s 项风险，其中高风险 %s 项、中风险 %s 项、低风险 %s 项。"
        % (total, high, medium, low)
    )
    if high:
        summary += " 高风险项不得直接自动定稿，必须由法务结合完整原文、最新有效法规及业务事实复核。"
    if unresolved:
        summary += " 另有 %s 个分段分析失败或结果不完整，应补充人工审查。" % len(
            unresolved
        )

    priority = []
    for risk in deduped:
        if risk.get("risk_level") not in ("高", "中"):
            continue
        priority.append(
            {
                "risk_id": risk["risk_id"],
                "risk_level": risk["risk_level"],
                "title": risk["title"],
                "recommendation": risk.get("recommendation") or "交由法务复核",
                "location": risk.get("location", {}).get("locator_label") or "未定位",
            }
        )
        if len(priority) >= 10:
            break

    report = {
        "report_meta": {
            "schema_version": "1.0.0",
            "generator": "Dify 文档法律与合规风险审查 MVP",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "locator_quality": locator_quality,
            "preprocess_notice": preprocess_notice,
            "document_char_count": document_char_count,
            "chunk_count": chunk_count,
            "legal_disclaimer": "本报告仅供内部辅助审查，不构成正式法律意见。",
        },
        "review_parameters": {
            "declared_doc_type": declared_doc_type,
            "industry": industry,
            "jurisdiction": jurisdiction,
            "strictness": strictness,
            "focus_risk_types": focus_risk_types,
        },
        "document_profile": profile,
        "risk_summary": {
            "counts": {
                "total": total,
                "high": high,
                "medium": medium,
                "low": low,
            },
            "executive_summary": summary,
            "high_risk_requires_legal_review": high > 0,
            "unresolved_chunk_count": len(unresolved),
        },
        "priority_remediation": priority,
        "risks": deduped,
        "unresolved_chunks": unresolved,
        "retrieval_trace": _merge_unique(
            traces,
            lambda item: (
                _norm(item.get("title")),
                _norm(item.get("content"))[:120],
            ),
        )[:100],
    }
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    report_markdown = _render_markdown(report)
    return {
        "report_markdown": report_markdown,
        "report_json": report_json,
        "high_risk_count": high,
        "manual_review_required": bool(
            high
            or unresolved
            or any(item.get("needs_manual_review") for item in deduped)
        ),
    }
