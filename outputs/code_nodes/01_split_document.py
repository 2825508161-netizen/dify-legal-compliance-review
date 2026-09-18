import re


MAX_CHARS = 5500
MAX_PARAGRAPHS = 5
OVERLAP_PARAGRAPHS = 1
MAX_CHUNKS = 120


def _clean(value):
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _detect_page_marker(line):
    patterns = [
        r"^\s*\[PAGE\s*(\d+)\]\s*$",
        r"^\s*[-—]{2,}\s*PAGE\s*(\d+)\s*[-—]{2,}\s*$",
        r"^\s*第\s*(\d+)\s*页\s*$",
        r"^\s*PAGE\s*(\d+)\s*$",
    ]
    for pattern in patterns:
        match = re.match(pattern, line, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _split_paragraphs(text):
    # Form feed is the only page delimiter that some PDF parsers preserve.
    text = text.replace("\f", "\n\n[[PAGE_BREAK]]\n\n")
    blocks = re.split(r"\n\s*\n+", text)
    paragraphs = []
    page = None
    paragraph_no = 0

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if block == "[[PAGE_BREAK]]":
            page = 1 if page is None else page + 1
            continue

        marker_page = _detect_page_marker(block)
        if marker_page is not None:
            page = marker_page
            continue

        # If extraction only preserved line breaks, keep headings and clauses locatable.
        pieces = [block]
        if len(block) > 2200:
            pieces = [
                item.strip()
                for item in re.split(r"(?<=[。！？；;])\s*|\n+", block)
                if item.strip()
            ]

        for piece in pieces:
            if len(piece) <= 2200:
                paragraph_no += 1
                paragraphs.append(
                    {
                        "paragraph_no": paragraph_no,
                        "page": page,
                        "text": piece,
                    }
                )
                continue

            # A single huge paragraph is split mechanically while retaining paragraph identity.
            start = 0
            part = 1
            while start < len(piece):
                paragraph_no += 1
                paragraphs.append(
                    {
                        "paragraph_no": paragraph_no,
                        "page": page,
                        "text": piece[start : start + 2000],
                        "part": part,
                    }
                )
                start += 1900
                part += 1

    return paragraphs


def _locator(paragraph):
    """Return a readable locator that can also be copied into the report."""
    page = paragraph.get("page")
    paragraph_no = paragraph["paragraph_no"]
    if page is None:
        return "第%s段" % paragraph_no
    return "第%s页·第%s段" % (page, paragraph_no)


def _range_locator(first, last):
    first_page = first.get("page")
    last_page = last.get("page")
    first_paragraph = first["paragraph_no"]
    last_paragraph = last["paragraph_no"]

    if first_paragraph == last_paragraph:
        return _locator(first)
    if first_page is None and last_page is None:
        return "第%s—%s段" % (first_paragraph, last_paragraph)
    if first_page is not None and first_page == last_page:
        return "第%s页·第%s—%s段" % (
            first_page,
            first_paragraph,
            last_paragraph,
        )
    return "%s—%s" % (_locator(first), _locator(last))


def _build_chunks(paragraphs):
    chunks = []
    current = []
    current_chars = 0

    def flush():
        nonlocal current, current_chars
        if not current:
            return
        chunk_no = len(chunks) + 1
        first = current[0]
        last = current[-1]
        pages = [p["page"] for p in current if p.get("page") is not None]
        decorated = "\n\n".join(
            "[%s] %s" % (_locator(p), p["text"]) for p in current
        )
        chunks.append(
            {
                "chunk_id": "C%03d" % chunk_no,
                "locator_label": _range_locator(first, last),
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "paragraph_start": first["paragraph_no"],
                "paragraph_end": last["paragraph_no"],
                "text": decorated,
            }
        )
        overlap = current[-OVERLAP_PARAGRAPHS:] if OVERLAP_PARAGRAPHS else []
        current = list(overlap)
        current_chars = sum(len(p["text"]) + 24 for p in current)

    for paragraph in paragraphs:
        projected = current_chars + len(paragraph["text"]) + 24
        # Limit both text size and clause count. Legal review output is often
        # much longer than its input; smaller batches prevent the model from
        # exhausting its response limit and returning truncated JSON.
        if current and (
            projected > MAX_CHARS or len(current) >= MAX_PARAGRAPHS
        ):
            flush()
            if len(chunks) >= MAX_CHUNKS:
                break
        current.append(paragraph)
        current_chars += len(paragraph["text"]) + 24

    if current and len(chunks) < MAX_CHUNKS:
        flush()

    return chunks


def main(
    extracted_text: str,
    declared_doc_type: str = "自动识别",
    industry: str = "未指定",
    jurisdiction: str = "中国大陆",
    strictness: str = "标准",
    focus_risk_types: str = "全部",
) -> dict:
    text = _clean(extracted_text)
    if not text:
        return {
            "chunks": [],
            "document_head": "",
            "document_char_count": 0,
            "chunk_count": 0,
            "locator_quality": "unavailable",
            "preprocess_notice": "未提取到文本，请检查文件是否为扫描件、加密文件或不受支持的格式。",
        }

    paragraphs = _split_paragraphs(text)
    chunks = _build_chunks(paragraphs)
    has_page = any(p.get("page") is not None for p in paragraphs)
    truncated = len(chunks) >= MAX_CHUNKS and chunks[-1]["paragraph_end"] < len(paragraphs)

    header = (
        "用户声明文档类型：%s\n所属行业：%s\n适用法域：%s\n"
        "审查严格程度：%s\n关注风险：%s\n\n"
        % (
            declared_doc_type,
            industry,
            jurisdiction,
            strictness,
            focus_risk_types,
        )
    )
    head_body = "\n\n".join(
        "[%s] %s" % (_locator(p), p["text"]) for p in paragraphs
    )[:12000]

    notices = []
    if not has_page:
        notices.append(
            "Dify 原生文档提取结果未提供可靠分页标记；本次按中文段落编号定位。需要精确页码时，请接入能够保留分页信息的解析服务。"
        )
    if truncated:
        notices.append(
            "文档超过 MVP 的 120 个分段上限，尾部未进入模型审查；请拆分文档后重跑。"
        )

    return {
        "chunks": chunks,
        "document_head": header + head_body,
        "document_char_count": len(text),
        "chunk_count": len(chunks),
        "locator_quality": "page_and_paragraph" if has_page else "paragraph_only",
        "preprocess_notice": "；".join(notices) if notices else "文本已完成分页/段落定位。",
    }
