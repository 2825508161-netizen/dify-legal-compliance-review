import json
import math
import re


LEVEL_ORDER = {"低": 1, "中": 2, "高": 3}
ALLOWED_LEVELS = set(LEVEL_ORDER)
ALLOWED_CATEGORIES = {
    "合同权利义务",
    "主体与授权",
    "价款与税务",
    "期限与终止",
    "违约责任",
    "争议解决",
    "劳动用工",
    "数据与隐私",
    "网络安全",
    "跨境合规",
    "反腐败",
    "反洗钱",
    "广告宣传",
    "消费者权益",
    "知识产权",
    "产品与行业监管",
    "供应商与第三方",
    "公司治理与制度",
    "其他",
}


def _json_objects(text):
    """Yield balanced JSON objects, ignoring braces inside quoted strings."""
    starts = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            starts.append(index)
        elif char == "}" and starts:
            start = starts.pop()
            if not starts:
                yield text[start : index + 1]


def _parse_model_json(text):
    value = (text or "").strip()
    # DeepSeek Flash may put reasoning in a think block when Dify's reasoning
    # separation is disabled. It is never user-facing and must not confuse JSON parsing.
    value = re.sub(r"<think\b[^>]*>.*?</think>", "", value, flags=re.I | re.S)
    value = re.sub(r"<!--\s*dify-deepseek-reasoning\s*-->", "", value, flags=re.I)
    value = re.sub(r"```(?:json)?", "", value, flags=re.I)
    # Scan from every opening brace with the standard decoder. This is more
    # tolerant than one global brace stack: DeepSeek reasoning can contain an
    # unmatched example brace before the final, otherwise valid JSON object.
    decoder = json.JSONDecoder()
    parsed_candidates = []
    for index, char in enumerate(value):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            parsed_candidates.append(parsed)

    # Keep the final answer when reasoning contains earlier JSON examples.
    for parsed in reversed(parsed_candidates):
        if isinstance(parsed.get("risks"), list):
            return parsed
        for key in ("result", "data", "analysis"):
            nested = parsed.get(key)
            if isinstance(nested, dict) and isinstance(nested.get("risks"), list):
                return nested

    # Backward compatibility for Flash responses that return a bare risk list.
    for parsed in reversed(parsed_candidates):
        for key in ("risk_items", "risk_list"):
            if isinstance(parsed.get(key), list):
                parsed["risks"] = parsed[key]
                return parsed

    # If the provider stopped before closing the outer JSON object, recover
    # every individually complete risk object. Incomplete objects are ignored.
    recovered = []
    seen = set()
    for parsed in parsed_candidates:
        if not (
            parsed.get("risk_category")
            and parsed.get("title")
            and parsed.get("original_excerpt")
        ):
            continue
        key = (
            str(parsed.get("risk_category")),
            str(parsed.get("original_excerpt")),
        )
        if key in seen:
            continue
        seen.add(key)
        recovered.append(parsed)
    if recovered:
        return {
            "risks": recovered,
            "extracted_elements": {},
            "_partial_model_output": True,
        }
    raise ValueError("未找到包含 risks 数组的合法 JSON 对象")


def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _display_location(page_start, page_end, paragraph_start, paragraph_end):
    page_start = _as_int(page_start)
    page_end = _as_int(page_end)
    paragraph_start = _as_int(paragraph_start)
    paragraph_end = _as_int(paragraph_end)

    if paragraph_start is None:
        if page_start is None:
            return "页码待定位·位置待核验"
        page_end = page_end or page_start
        return (
            "第%s页·段落待核验" % page_start
            if page_start == page_end
            else "第%s—%s页·段落待核验" % (page_start, page_end)
        )
    paragraph_end = paragraph_end or paragraph_start
    if page_start is None:
        if paragraph_start == paragraph_end:
            return "页码待定位·第%s段" % paragraph_start
        return "页码待定位·第%s—%s段" % (
            paragraph_start,
            paragraph_end,
        )
    page_end = page_end or page_start
    if page_start == page_end:
        if paragraph_start == paragraph_end:
            return "第%s页·第%s段" % (page_start, paragraph_start)
        return "第%s页·第%s—%s段" % (
            page_start,
            paragraph_start,
            paragraph_end,
        )
    return "第%s页·第%s段—第%s页·第%s段" % (
        page_start,
        paragraph_start,
        page_end,
        paragraph_end,
    )


def _safe_confidence(value):
    """Return a bounded confidence plus a user-visible adjustment warning."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0.5, "模型未提供有效置信度，已使用默认值 0.5"
    if isinstance(value, bool):
        return 0.5, "模型置信度不是有效数字，已使用默认值 0.5"
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.5, "模型置信度不是有效数字，已使用默认值 0.5"
    if not math.isfinite(confidence):
        return 0.5, "模型置信度不是有限数字，已使用默认值 0.5"
    if confidence < 0.0:
        return 0.0, "模型置信度低于 0，已调整为 0"
    if confidence > 1.0:
        return 1.0, "模型置信度高于 1，已调整为 1"
    return confidence, ""


def _valid_positive_range(start, end):
    start = _as_int(start)
    end = _as_int(end)
    if start is None or start <= 0:
        return None, None
    end = end if end is not None else start
    if end < start:
        return None, None
    return start, end


def _verified_location(chunk, model_location):
    """Use document-derived bounds; never trust an unconstrained model page."""
    model_location = model_location if isinstance(model_location, dict) else {}
    page_start, page_end = _valid_positive_range(
        chunk.get("page_start"), chunk.get("page_end")
    )
    paragraph_start, paragraph_end = _valid_positive_range(
        chunk.get("paragraph_start"), chunk.get("paragraph_end")
    )

    model_page_start, model_page_end = _valid_positive_range(
        model_location.get("page_start"), model_location.get("page_end")
    )
    model_paragraph_start, model_paragraph_end = _valid_positive_range(
        model_location.get("paragraph_start"),
        model_location.get("paragraph_end"),
    )

    warnings = []
    if model_page_start is not None:
        if page_start is None:
            warnings.append("模型提供的页码无法由文档分段确认，已标记为页码待定位")
        elif (model_page_start, model_page_end) != (page_start, page_end):
            warnings.append("模型页码与文档分段位置不一致，已改用文档分段页码")

    if paragraph_start is not None and model_paragraph_start is not None:
        if not (
            paragraph_start <= model_paragraph_start <= paragraph_end
            and paragraph_start <= model_paragraph_end <= paragraph_end
        ):
            warnings.append("模型段落号超出当前分段范围，已改用文档分段位置")
        else:
            paragraph_start = model_paragraph_start
            paragraph_end = model_paragraph_end

    warning = "；".join(warnings)
    return {
        "chunk_id": chunk.get("chunk_id"),
        "page_start": page_start,
        "page_end": page_end,
        "page_verification_status": "source_verified" if page_start else "待定位",
        "paragraph_start": paragraph_start,
        "paragraph_end": paragraph_end,
        "model_location_adjusted": bool(warnings),
        "locator_label": _display_location(
            page_start,
            page_end,
            paragraph_start,
            paragraph_end,
        ),
    }, warning


def _knowledge_items(knowledge_result):
    """Accept either the built-in retrieval array or Knowledge API JSON body."""
    value = knowledge_result
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []

    if isinstance(value, list):
        return value

    if not isinstance(value, dict):
        return []

    normalized_items = value.get("source_items")
    if isinstance(normalized_items, list):
        return normalized_items

    # HTTP Request can sometimes be passed as the complete node output.
    body = value.get("body")
    if isinstance(body, str):
        try:
            value = json.loads(body)
        except Exception:
            return []

    records = value.get("records") if isinstance(value, dict) else None
    if not isinstance(records, list):
        return []

    items = []
    for record in records:
        if not isinstance(record, dict):
            continue
        segment = record.get("segment") or {}
        document = segment.get("document") or {}
        metadata = {
            "dataset_id": segment.get("dataset_id"),
            "document_id": segment.get("document_id") or document.get("id"),
            "document_name": document.get("name")
            or segment.get("document_name")
            or "未命名知识库资料",
            "segment_id": segment.get("id"),
            "position": segment.get("position"),
            "score": record.get("score"),
        }
        items.append(
            {
                "title": metadata["document_name"],
                "content": segment.get("content") or record.get("content") or "",
                "metadata": metadata,
            }
        )
    return items


def _source_list(knowledge_result):
    sources = []
    for item in _knowledge_items(knowledge_result):
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        title = (
            metadata.get("document_name")
            or metadata.get("title")
            or item.get("title")
            or "未命名知识库资料"
        )
        sources.append(
            {
                "title": str(title),
                "content": str(item.get("content") or "")[:1800],
                "metadata": metadata,
            }
        )
    return sources


def _text_in_chunk(excerpt, chunk_text):
    if not excerpt:
        return False
    normalize = lambda s: re.sub(r"\s+", "", str(s))
    return normalize(excerpt) in normalize(chunk_text)


def _law_supported(base, sources):
    title = str(base.get("title") or "").strip()
    article = str(base.get("article") or "").strip()
    quote = str(base.get("source_quote") or "").strip()
    if not title or not quote:
        return False
    haystacks = [
        re.sub(r"\s+", "", source["title"] + source["content"])
        for source in sources
    ]
    title_key = re.sub(r"\s+", "", title)
    quote_key = re.sub(r"\s+", "", quote)
    article_key = re.sub(r"\s+", "", article)
    return any(
        title_key in hay
        and quote_key[:24] in hay
        and (not article_key or article_key in hay)
        for hay in haystacks
    )


def _fallback(chunk, reason, sources):
    return {
        "chunk_id": chunk.get("chunk_id", "UNKNOWN"),
        "analysis_status": "failed",
        "failure_reason": reason,
        "risks": [],
        "extracted_elements": {},
        "retrieval_trace": sources,
        "needs_manual_review": True,
    }


def main(
    raw_analysis: str,
    chunk: dict,
    knowledge_result,
    retrieval_status: str = "",
    retrieval_warning: str = "",
) -> dict:
    if isinstance(knowledge_result, dict):
        retrieval_status = retrieval_status or str(
            knowledge_result.get("retrieval_status") or ""
        )
        retrieval_warning = retrieval_warning or str(
            knowledge_result.get("retrieval_warning") or ""
        )
    retrieval_status = retrieval_status or "success"
    retrieval_problem = retrieval_status in {"empty", "error", "timeout"}
    sources = _source_list(knowledge_result)
    try:
        parsed = _parse_model_json(raw_analysis)
    except Exception as exc:
        return {
            "result_json": json.dumps(
                _fallback(chunk, "模型 JSON 解析失败：%s" % exc, sources),
                ensure_ascii=False,
            )
        }

    risks = parsed.get("risks") if isinstance(parsed, dict) else None
    if not isinstance(risks, list):
        return {
            "result_json": json.dumps(
                _fallback(chunk, "模型未返回 risks 数组", sources),
                ensure_ascii=False,
            )
        }

    partial_output = bool(parsed.get("_partial_model_output"))
    normalized = []
    analysis_warnings = []
    if partial_output:
        analysis_warnings.append(
            "模型响应未闭合；已恢复其中完整风险对象，建议结合原文人工复核。"
        )
    if retrieval_problem:
        warning = retrieval_warning or (
            "知识库检索状态为 %s，法律依据需人工复核" % retrieval_status
        )
        analysis_warnings.append(warning)
    for index, raw in enumerate(risks[:30], start=1):
        if not isinstance(raw, dict):
            continue
        level = raw.get("risk_level")
        if level not in ALLOWED_LEVELS:
            level = "中"
        category = raw.get("risk_category")
        if category not in ALLOWED_CATEGORIES:
            category = "其他"

        excerpt = str(raw.get("original_excerpt") or "").strip()[:1200]
        excerpt_verified = _text_in_chunk(excerpt, chunk.get("text", ""))
        legal_bases = []
        any_unverified_law = False
        for base in (raw.get("legal_bases") or [])[:8]:
            if not isinstance(base, dict):
                continue
            supported = _law_supported(base, sources)
            if not supported:
                any_unverified_law = True
            legal_bases.append(
                {
                    "title": str(base.get("title") or "未确认依据"),
                    "article": str(base.get("article") or "") or None,
                    "source_quote": str(base.get("source_quote") or "")[:1000],
                    "source_metadata": base.get("source_metadata") or {},
                    "verification_status": (
                        "retrieved_and_text_matched"
                        if supported
                        else "需人工复核"
                    ),
                }
            )

        if not legal_bases:
            any_unverified_law = True
            legal_bases = [
                {
                    "title": "未检索到可核验的明确依据",
                    "article": None,
                    "source_quote": "",
                    "source_metadata": {},
                    "verification_status": "需人工复核",
                }
            ]

        needs_manual = bool(raw.get("needs_manual_review"))
        needs_manual = (
            needs_manual
            or level == "高"
            or any_unverified_law
            or not excerpt_verified
            or partial_output
            or retrieval_problem
        )
        uncertainty = str(raw.get("uncertainty_reason") or "").strip()
        if not excerpt_verified:
            uncertainty = (
                uncertainty + "；原文摘录未通过分段文本精确匹配"
            ).strip("；")
        if any_unverified_law:
            uncertainty = (
                uncertainty + "；至少一项法律依据未通过知识库文本匹配"
            ).strip("；")
        if partial_output:
            uncertainty = (
                uncertainty + "；模型响应未闭合，系统仅恢复了其中完整的风险对象"
            ).strip("；")
        if retrieval_problem:
            uncertainty = (
                uncertainty
                + "；"
                + (
                    retrieval_warning
                    or "知识库检索状态为 %s" % retrieval_status
                )
            ).strip("；")

        location, location_warning = _verified_location(
            chunk,
            raw.get("location"),
        )
        confidence, confidence_warning = _safe_confidence(raw.get("confidence"))
        for warning in (location_warning, confidence_warning):
            if warning:
                uncertainty = (uncertainty + "；" + warning).strip("；")
                analysis_warnings.append(
                    "%s-R%02d：%s"
                    % (chunk.get("chunk_id", "UNKNOWN"), index, warning)
                )
        needs_manual = needs_manual or bool(location_warning or confidence_warning)
        normalized.append(
            {
                "risk_id": "%s-R%02d"
                % (chunk.get("chunk_id", "UNKNOWN"), index),
                "risk_category": category,
                "risk_level": level,
                "title": str(raw.get("title") or "未命名风险")[:200],
                "original_excerpt": excerpt,
                "excerpt_verified": excerpt_verified,
                "location": location,
                "issue": str(raw.get("issue") or "")[:2500],
                "legal_bases": legal_bases,
                "possible_consequences": str(
                    raw.get("possible_consequences") or ""
                )[:2000],
                "recommendation": str(raw.get("recommendation") or "")[:2500],
                "replacement_clause": str(
                    raw.get("replacement_clause") or ""
                )[:3000],
                "reasoning_summary": str(
                    raw.get("reasoning_summary") or ""
                )[:1200],
                "needs_manual_review": needs_manual,
                "uncertainty_reason": uncertainty,
                "confidence": confidence,
                "confidence_warning": confidence_warning,
            }
        )

    payload = {
        "chunk_id": chunk.get("chunk_id", "UNKNOWN"),
        "analysis_status": "ok",
        "failure_reason": "",
        "analysis_warning": "；".join(analysis_warnings),
        "risks": normalized,
        "extracted_elements": (
            parsed.get("extracted_elements")
            if isinstance(parsed.get("extracted_elements"), dict)
            else {}
        ),
        "retrieval_trace": sources,
        "retrieval_status": retrieval_status,
        "retrieval_warning": retrieval_warning,
        "needs_manual_review": retrieval_problem
        or any(item["needs_manual_review"] for item in normalized),
    }
    return {"result_json": json.dumps(payload, ensure_ascii=False)}
