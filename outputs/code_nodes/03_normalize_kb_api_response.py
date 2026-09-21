import json
import re


def _load(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _as_status(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _looks_like_timeout(value):
    text = str(value or "")
    return bool(
        re.search(
            r"(?:timeout|timed\s*out|deadline\s*exceeded|请求超时|连接超时)",
            text,
            flags=re.I,
        )
    )


def main(api_body: str, unused_status: int = 200) -> dict:
    wrapper = _load(api_body)
    invalid_response = wrapper is None
    wrapper = wrapper or {}
    embedded_status = wrapper.get("status_code", wrapper.get("status"))
    http_status = _as_status(embedded_status)
    if http_status is None:
        http_status = _as_status(unused_status)

    payload = wrapper
    if "body" in wrapper:
        body = wrapper.get("body")
        if isinstance(body, dict):
            payload = body
        elif isinstance(body, str):
            parsed_body = _load(body)
            if parsed_body is None:
                invalid_response = True
                payload = {}
            else:
                payload = parsed_body
        else:
            invalid_response = True
            payload = {}

    timeout = http_status in {408, 504, 524, 598, 599} or _looks_like_timeout(
        api_body
    )
    http_error = http_status is not None and not 200 <= http_status < 300

    records = payload.get("records")
    if not isinstance(records, list):
        if not timeout and not http_error:
            invalid_response = True
        records = []

    sources = []
    context_blocks = []
    for index, record in enumerate(records[:8], start=1):
        if not isinstance(record, dict):
            continue
        segment = record.get("segment") or {}
        document = segment.get("document") or {}
        content = str(segment.get("content") or record.get("content") or "").strip()
        if not content:
            continue
        title = str(
            document.get("name")
            or segment.get("document_name")
            or "未命名知识库资料"
        )
        metadata = {
            "dataset_id": segment.get("dataset_id"),
            "document_id": segment.get("document_id") or document.get("id"),
            "document_name": title,
            "segment_id": segment.get("id"),
            "position": segment.get("position"),
            "score": record.get("score"),
        }
        sources.append(
            {
                "title": title,
                "content": content[:3000],
                "metadata": metadata,
            }
        )
        context_blocks.append(
            "[检索来源 {index}]\n"
            "资料名称：{title}\n"
            "分段位置：{position}\n"
            "相关度：{score}\n"
            "内容：\n{content}".format(
                index=index,
                title=title,
                position=metadata.get("position"),
                score=metadata.get("score"),
                content=content[:3000],
            )
        )

    if timeout:
        retrieval_status = "timeout"
        retrieval_warning = "知识库检索请求超时"
        if http_status is not None:
            retrieval_warning += "（HTTP %s）" % http_status
        context_text = (
            retrieval_warning
            + "。本分段不得把任何法律依据标记为已核验；请在服务恢复后重试，"
            "当前结果必须标记为“需人工复核”。"
        )
    elif http_error:
        retrieval_status = "error"
        retrieval_warning = "知识库检索接口报错"
        if http_status is not None:
            retrieval_warning += "（HTTP %s）" % http_status
        context_text = (
            retrieval_warning
            + "。这不是“未检索到法规”，而是检索服务故障；本分段不得输出已核验"
            "法律依据，并应提示人工复核或稍后重试。"
        )
    elif invalid_response:
        retrieval_status = "error"
        retrieval_warning = "知识库检索接口返回了无法识别的响应"
        context_text = (
            retrieval_warning
            + "。这不是“未检索到法规”；本分段不得输出已核验法律依据，"
            "并应提示人工复核或稍后重试。"
        )
    elif context_blocks:
        retrieval_status = "success"
        retrieval_warning = ""
        context_text = (
            "以下内容来自法律与合规知识库。仅可引用其中明确出现的法规名称、"
            "条款编号和文字；不得补写未出现的法条。\n\n"
            + "\n\n---\n\n".join(context_blocks)
        )
    else:
        retrieval_status = "empty"
        retrieval_warning = "知识库检索请求成功，但没有返回可用的匹配片段"
        context_text = (
            retrieval_warning
            + "。所有法律依据必须标记为“需人工复核”，不得凭模型记忆补写"
            "法条或案例编号。"
        )

    if retrieval_status in {"error", "timeout"}:
        sources = []

    return {
        "context_text": context_text,
        "source_items": sources,
        "source_count": len(sources),
        "retrieval_status": retrieval_status,
        "retrieval_warning": retrieval_warning,
        "http_status": http_status,
    }
