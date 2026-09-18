import json


def _load(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def main(api_body: str, unused_status: int = 200) -> dict:
    payload = _load(api_body)
    if isinstance(payload.get("body"), str):
        payload = _load(payload["body"])

    records = payload.get("records")
    if not isinstance(records, list):
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

    if context_blocks:
        context_text = (
            "以下内容来自法律与合规知识库。仅可引用其中明确出现的法规名称、"
            "条款编号和文字；不得补写未出现的法条。\n\n"
            + "\n\n---\n\n".join(context_blocks)
        )
    else:
        context_text = (
            "知识库本次未返回可用片段。所有法律依据必须标记为“需人工复核”，"
            "不得凭模型记忆补写法条或案例编号。"
        )

    return {
        "context_text": context_text,
        "source_items": sources,
        "source_count": len(sources),
    }
