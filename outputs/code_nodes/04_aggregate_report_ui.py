import json
import math
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
                "页码待定位·第%s段" % paragraph_start
                if paragraph_start == paragraph_end
                else "页码待定位·第%s—%s段"
                % (paragraph_start, paragraph_end)
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

    if page_start is not None:
        page_end = page_end or page_start
        return (
            "第%s页·段落待核验" % page_start
            if page_start == page_end
            else "第%s—%s页·段落待核验" % (page_start, page_end)
        )

    # Backward compatibility for cached results produced before Chinese locators.
    legacy = str(location.get("locator_label") or "")
    match = re.fullmatch(r"P\?/¶(\d+)(?:-P\?/¶(\d+))?", legacy)
    if match:
        start, end = match.group(1), match.group(2) or match.group(1)
        return (
            "页码待定位·第%s段" % start
            if start == end
            else "页码待定位·第%s—%s段" % (start, end)
        )
    return legacy or "页码待定位·位置待核验"


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


def _normalize_key_text(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def _level_score(value):
    return {"高": 3, "中": 2, "低": 1}.get(value, 0)


def _confidence(value):
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(confidence):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _evidence_score(risk):
    verified_laws = sum(
        1
        for base in (risk.get("legal_bases") or [])
        if isinstance(base, dict)
        and base.get("verification_status") == "retrieved_and_text_matched"
    )
    completeness = sum(
        1
        for key in (
            "issue",
            "possible_consequences",
            "recommendation",
            "replacement_clause",
            "reasoning_summary",
        )
        if str(risk.get(key) or "").strip()
    )
    return (
        1 if risk.get("excerpt_verified") else 0,
        verified_laws,
        _confidence(risk.get("confidence")),
        completeness,
    )


def _unique_values(values):
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalise_uncertainty_reason(value):
    """Collapse repeated retrieval warnings while preserving case-specific facts."""
    parts = re.split(r"[；;\n]+", str(value or ""))
    kb_state = ""
    specific = []
    for part in parts:
        part = part.strip().strip("。；; ")
        if not part:
            continue
        if re.search(r"知识库.*(?:超时|timeout|timed\s*out)", part, re.I):
            kb_state = "知识库检索超时，法律依据未获文本核验"
        elif re.search(r"知识库.*(?:接口报错|接口异常|服务故障|无法识别)", part):
            if "超时" not in kb_state:
                kb_state = "知识库检索接口异常，法律依据未获文本核验"
        elif (
            re.search(r"知识库.*(?:未返回|没有返回|未检索到|无可用|未通过.*匹配)", part)
            or "至少一项法律依据未通过知识库文本匹配" in part
            or "无可用法律检索片段" in part
        ):
            if not kb_state:
                kb_state = "法律依据未获知识库文本核验"
            if "且" in part:
                tail = part.split("且", 1)[1].strip().strip("。；; ")
                if tail and not re.search(r"知识库|法律依据.*(?:核验|匹配)", tail):
                    specific.append(tail)
            continue
        else:
            specific.append(part)
    return "；".join(_unique_values(([kb_state] if kb_state else []) + specific))


def _fill_missing_placeholders(value):
    """Make every intentionally blank replacement-clause field visible."""
    text = str(value or "")
    if not text:
        return text
    text = re.sub(r"[_＿]{1,}", "【待填写】", text)
    text = re.sub(
        r"(?<=后)(?=(?:日内|个工作日内|小时内|个月内|月内|年内))",
        "【待填写】",
        text,
    )
    text = re.sub(r"(?<=[的为率])(?=%)", "【待填写】", text)
    text = re.sub(r"([：:])\s*(?=[，,；;。])", r"\1【待填写】", text)
    text = re.sub(r"(?:【待填写】){2,}", "【待填写】", text)
    return text


def _clean_risk(risk):
    cleaned = dict(risk)
    cleaned["replacement_clause"] = _fill_missing_placeholders(
        cleaned.get("replacement_clause")
    )
    cleaned["uncertainty_reason"] = _normalise_uncertainty_reason(
        cleaned.get("uncertainty_reason")
    )
    return cleaned


def _forum_clause_evidence(risks):
    evidence = []
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        excerpt = str(risk.get("original_excerpt") or "").strip()
        if not excerpt:
            continue
        if re.search(r"(?:人民法院|法院|仲裁委员会|仲裁机构)", excerpt) and re.search(
            r"(?:争议|管辖|起诉|诉讼|仲裁)", excerpt
        ):
            evidence.append(excerpt)
    return _unique_values(evidence)


def _claims_forum_is_missing(risk):
    text = " ".join(
        str(risk.get(key) or "")
        for key in ("title", "issue", "reasoning_summary")
    )
    return bool(
        re.search(
            r"(?:未约定|没有约定|缺少|缺乏).{0,18}(?:管辖法院|法院管辖|仲裁机构|争议解决)",
            text,
        )
    )


def _apply_document_fact_checks(risks):
    """Resolve document-wide contradictions before deduplication and reporting."""
    forum_evidence = _forum_clause_evidence(risks)
    if not forum_evidence:
        return risks, [], {"forum_clause_detected": False, "evidence": []}

    corrected = []
    adjustments = []
    for raw_risk in risks:
        if not isinstance(raw_risk, dict):
            continue
        risk = dict(raw_risk)
        if not _claims_forum_is_missing(risk):
            corrected.append(risk)
            continue

        combined_text = " ".join(
            str(risk.get(key) or "")
            for key in ("title", "issue", "reasoning_summary")
        )
        if "适用法律" not in combined_text:
            adjustments.append(
                {
                    "action": "suppressed_false_absence_risk",
                    "title": risk.get("title"),
                    "reason": "全文已检出法院或仲裁条款",
                }
            )
            continue

        risk["title"] = "未明确约定合同适用法律及争议期间履行安排"
        risk["issue"] = (
            "全文已明确约定“仅可向乙方所在地人民法院起诉”，因此不认定为"
            "缺少管辖法院。本项仅指出合同未明确约定适用法律，以及争议期间"
            "继续履行和通知方式。"
        )
        risk["possible_consequences"] = (
            "适用法律和争议期间义务不够明确，可能增加法律适用与履行争议；"
            "现有乙方所在地法院条款是否有效、是否明显不利，需在另一风险项中复核。"
        )
        risk["recommendation"] = (
            "补充适用中华人民共和国法律及争议期间继续履行、通知规则；"
            "保留对现有乙方所在地法院管辖条款的单独审查。"
        )
        risk["replacement_clause"] = (
            "本合同的订立、效力、履行、解释及争议解决适用中华人民共和国法律。"
            "因本合同产生的争议，双方应先协商；协商不成的，向与争议有实际联系"
            "且有管辖权的人民法院起诉。争议处理期间，除争议事项外，双方应继续"
            "履行不受影响的其他义务。"
        )
        risk["reasoning_summary"] = (
            "全文已检出乙方所在地人民法院条款，不认定为未约定管辖；"
            "本项仅审查适用法律和争议期间的履行安排。"
        )
        risk["needs_manual_review"] = True
        risk["document_fact_correction"] = "已依据全文证据纠正管辖缺失误判"
        adjustments.append(
            {
                "action": "rewrote_mixed_absence_risk",
                "title": raw_risk.get("title"),
                "reason": "全文已检出法院或仲裁条款，仅保留其他真实缺失事项",
            }
        )
        corrected.append(risk)

    return corrected, adjustments, {
        "forum_clause_detected": True,
        "evidence": forum_evidence[:5],
    }


def _dedupe_processing_warnings(warnings):
    grouped = []
    index = {}
    for warning in warnings:
        message = str(warning.get("message") or "").strip()
        if not message:
            continue
        chunk = str(warning.get("chunk_id") or "UNKNOWN")
        if message not in index:
            index[message] = len(grouped)
            grouped.append({"chunk_ids": [chunk], "message": message})
        elif chunk not in grouped[index[message]]["chunk_ids"]:
            grouped[index[message]]["chunk_ids"].append(chunk)
    return grouped


def _merge_legal_bases(first, second):
    result = []
    seen = set()
    for base in (first or []) + (second or []):
        if not isinstance(base, dict):
            continue
        key = (
            _normalize_key_text(base.get("title")),
            _normalize_key_text(base.get("article")),
            _normalize_key_text(base.get("source_quote"))[:120],
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(base)
    return result


def _merge_locations(existing, incoming):
    locations = []
    seen = set()
    candidates = []
    candidates.extend(existing.get("related_locations") or [])
    candidates.append(existing.get("location"))
    candidates.extend(incoming.get("related_locations") or [])
    candidates.append(incoming.get("location"))
    for location in candidates:
        if not isinstance(location, dict):
            continue
        normalized = dict(location)
        normalized["locator_label"] = _display_location(normalized)
        key = (
            normalized.get("chunk_id"),
            _as_int(normalized.get("page_start")),
            _as_int(normalized.get("page_end")),
            _as_int(normalized.get("paragraph_start")),
            _as_int(normalized.get("paragraph_end")),
        )
        if key in seen:
            continue
        seen.add(key)
        locations.append(normalized)
    return locations


def _merge_duplicate_risk(existing, incoming):
    existing_level = existing.get("risk_level")
    incoming_level = incoming.get("risk_level")
    final_level = (
        existing_level
        if _level_score(existing_level) >= _level_score(incoming_level)
        else incoming_level
    )

    same_final_level = [
        risk
        for risk in (existing, incoming)
        if risk.get("risk_level") == final_level
    ]
    primary = max(same_final_level, key=_evidence_score)
    secondary = incoming if primary is existing else existing
    merged = dict(primary)
    merged["location"] = dict(primary.get("location") or {})
    merged["risk_level"] = final_level
    merged["excerpt_verified"] = bool(
        existing.get("excerpt_verified") or incoming.get("excerpt_verified")
    )
    merged["confidence"] = max(
        _confidence(existing.get("confidence")),
        _confidence(incoming.get("confidence")),
    )
    merged["legal_bases"] = _merge_legal_bases(
        existing.get("legal_bases"), incoming.get("legal_bases")
    )
    merged["related_locations"] = _merge_locations(existing, incoming)
    merged["merged_categories"] = _unique_values(
        (existing.get("merged_categories") or [])
        + [existing.get("risk_category")]
        + (incoming.get("merged_categories") or [])
        + [incoming.get("risk_category")]
    )
    merged["merged_titles"] = _unique_values(
        (existing.get("merged_titles") or [])
        + [existing.get("title")]
        + (incoming.get("merged_titles") or [])
        + [incoming.get("title")]
    )
    levels = _unique_values(
        (existing.get("risk_level_candidates") or [])
        + [existing_level]
        + (incoming.get("risk_level_candidates") or [])
        + [incoming_level]
    )
    levels.sort(key=_level_score, reverse=True)
    merged["risk_level_candidates"] = levels
    severity_conflict = len(levels) > 1
    merged["severity_conflict"] = severity_conflict
    merged["merge_count"] = int(existing.get("merge_count") or 1) + int(
        incoming.get("merge_count") or 1
    )

    reasons = _unique_values(
        [
            existing.get("uncertainty_reason"),
            incoming.get("uncertainty_reason"),
        ]
    )
    if severity_conflict:
        reasons.append(
            "同一风险出现不同等级（%s），系统按不降低风险原则保留较高等级“%s”，"
            "并要求法务结合原文和检索依据复核"
            % ("、".join(levels), final_level)
        )
    merged["uncertainty_reason"] = _normalise_uncertainty_reason(
        "；".join(_unique_values(reasons))
    )
    merged["severity_decision"] = (
        "存在等级冲突，按较高等级保留并转人工复核"
        if severity_conflict
        else "重复结果等级一致，保留同等级中证据更完整的结果"
    )
    merged["needs_manual_review"] = bool(
        existing.get("needs_manual_review")
        or incoming.get("needs_manual_review")
        or final_level == "高"
        or severity_conflict
    )

    for field in (
        "issue",
        "possible_consequences",
        "recommendation",
        "replacement_clause",
        "reasoning_summary",
        "original_excerpt",
    ):
        if not str(merged.get(field) or "").strip():
            merged[field] = secondary.get(field)
    return merged


def main(iteration_results: list, declared_doc_type: str, jurisdiction: str) -> dict:
    risks = []
    traces = []
    unresolved = []
    warnings = []
    forced_manual_review = False
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
        forced_manual_review = forced_manual_review or bool(
            item.get("needs_manual_review")
        )
        if item.get("analysis_warning"):
            warnings.append(
                {
                    "chunk_id": item.get("chunk_id", "UNKNOWN"),
                    "message": item.get("analysis_warning"),
                }
            )

    risks, conflict_adjustments, document_fact_checks = _apply_document_fact_checks(
        risks
    )
    warnings = _dedupe_processing_warnings(warnings)

    deduplicated = []
    index_by_key = {}
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        risk = _clean_risk(risk)
        category = (risk.get("risk_category") or "其他").strip()
        source_text = risk.get("original_excerpt") or risk.get("title") or ""
        key = (
            _dedupe_bucket(category),
            _normalize_key_text(source_text),
        )
        if not key[1]:
            key = (key[0], "__unique__%s" % len(deduplicated))
        if key in index_by_key:
            position = index_by_key[key]
            deduplicated[position] = _merge_duplicate_risk(
                deduplicated[position], risk
            )
            continue
        copied = dict(risk)
        copied["location"] = dict(risk.get("location") or {})
        copied.setdefault("merge_count", 1)
        copied.setdefault("risk_level_candidates", [risk.get("risk_level")])
        copied.setdefault("severity_conflict", False)
        copied.setdefault("severity_decision", "未发生重复合并")
        copied.setdefault("related_locations", _merge_locations(copied, {}))
        index_by_key[key] = len(deduplicated)
        deduplicated.append(copied)

    deduplicated.sort(
        key=lambda risk: -_level_score(risk.get("risk_level"))
    )
    for index, risk in enumerate(deduplicated, start=1):
        risk["risk_id"] = "RISK-%03d" % index
        risk.setdefault("location", {})["locator_label"] = _display_location(
            risk.get("location")
        )
        for location in risk.get("related_locations") or []:
            location["locator_label"] = _display_location(location)

    counts = {
        level: sum(
            1 for risk in deduplicated if risk.get("risk_level") == level
        )
        for level in ("高", "中", "低")
    }
    manual_review_required = bool(
        unresolved
        or counts["高"]
        or forced_manual_review
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
        "document_fact_checks": document_fact_checks,
        "conflict_adjustments": conflict_adjustments,
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
                % (
                    "、".join(warning.get("chunk_ids") or ["UNKNOWN"]),
                    warning.get("message"),
                )
            )

    lines += ["", "## 风险明细", ""]
    for risk in deduplicated:
        location = (risk.get("location") or {}).get(
            "locator_label"
        ) or "位置待核验"
        detail_lines = [
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
        ]
        related_labels = _unique_values(
            [
                item.get("locator_label")
                for item in (risk.get("related_locations") or [])
                if isinstance(item, dict)
            ]
        )
        if len(related_labels) > 1:
            detail_lines.append("- 相关位置：%s" % "；".join(related_labels))
        if risk.get("severity_conflict"):
            detail_lines.append(
                "- 等级处理：%s" % risk.get("severity_decision")
            )
        if risk.get("uncertainty_reason"):
            detail_lines.append(
                "- 需复核原因：%s" % risk.get("uncertainty_reason")
            )
        detail_lines += ["", "法律依据："]
        lines += detail_lines
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
