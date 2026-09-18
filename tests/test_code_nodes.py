import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CODE_NODES = ROOT / "outputs" / "code_nodes"


def load_module(filename):
    path = CODE_NODES / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


splitter = load_module("01_split_document.py")
query_builder = load_module("00_build_kb_query.py")
normalizer = load_module("02_normalize_chunk_result.py")
aggregator = load_module("04_aggregate_report_ui.py")


class SplitDocumentTests(unittest.TestCase):
    def test_uses_chinese_paragraph_locator_without_fake_pages(self):
        result = splitter.main("第一条 甲方付款。\n\n第二条 乙方交付。")
        self.assertEqual(result["locator_quality"], "paragraph_only")
        self.assertEqual(result["chunk_count"], 1)
        self.assertEqual(result["chunks"][0]["locator_label"], "第1—2段")
        self.assertNotIn("P?/", result["chunks"][0]["text"])

    def test_preserves_explicit_page_marker(self):
        result = splitter.main("第 2 页\n\n第一条 甲方付款。")
        chunk = result["chunks"][0]
        self.assertEqual(chunk["page_start"], 2)
        self.assertEqual(chunk["locator_label"], "第2页·第1段")


class QueryTests(unittest.TestCase):
    def test_builds_short_topic_query(self):
        chunk = {"text": "乙方可跨境传输个人信息，并免责。"}
        query = query_builder.main(chunk, "数据隐私")["query"]
        self.assertIn("个人信息保护法", query)
        self.assertIn("民法典", query)
        self.assertLessEqual(len(query), 500)
        self.assertNotIn("乙方可跨境传输", query)


class NormalizeTests(unittest.TestCase):
    def test_strips_reasoning_and_verifies_excerpt_and_legal_basis(self):
        excerpt = "乙方可跨境传输个人信息"
        raw = {
            "risks": [
                {
                    "risk_category": "数据与隐私",
                    "risk_level": "高",
                    "title": "跨境传输缺少合规条件",
                    "original_excerpt": excerpt,
                    "location": {},
                    "issue": "缺少出境合规条件。",
                    "legal_bases": [
                        {
                            "title": "中华人民共和国个人信息保护法",
                            "article": "第三十八条",
                            "source_quote": "个人信息处理者因业务等需要，确需向中华人民共和国境外提供个人信息的",
                        }
                    ],
                    "possible_consequences": "监管整改。",
                    "recommendation": "补充合法条件。",
                    "replacement_clause": "满足法定条件后方可出境。",
                    "reasoning_summary": "条款允许无条件出境。",
                    "needs_manual_review": False,
                    "confidence": 0.9,
                }
            ],
            "extracted_elements": {},
        }
        raw_text = "<think>internal reasoning</think>\n" + json.dumps(
            raw, ensure_ascii=False
        )
        chunk = {
            "chunk_id": "C001",
            "text": "[第1段] " + excerpt,
            "page_start": None,
            "page_end": None,
            "paragraph_start": 1,
            "paragraph_end": 1,
        }
        sources = [
            {
                "title": "中华人民共和国个人信息保护法",
                "content": "第三十八条 个人信息处理者因业务等需要，确需向中华人民共和国境外提供个人信息的，应当具备法定条件。",
                "metadata": {"document_name": "中华人民共和国个人信息保护法"},
            }
        ]
        result = normalizer.main(raw_text, chunk, sources)
        payload = json.loads(result["result_json"])
        risk = payload["risks"][0]
        self.assertTrue(risk["excerpt_verified"])
        self.assertEqual(
            risk["legal_bases"][0]["verification_status"],
            "retrieved_and_text_matched",
        )
        self.assertEqual(risk["location"]["locator_label"], "第1段")
        self.assertTrue(risk["needs_manual_review"], "all high risks need review")


class AggregateTests(unittest.TestCase):
    def test_deduplicates_same_contract_excerpt_across_categories(self):
        excerpt = "甲方可随时解除合同且无需承担任何责任。"

        def risk(category, title):
            return {
                "risk_category": category,
                "risk_level": "高",
                "title": title,
                "original_excerpt": excerpt,
                "location": {
                    "page_start": None,
                    "page_end": None,
                    "paragraph_start": 2,
                    "paragraph_end": 2,
                    "locator_label": "P?/¶2",
                },
                "issue": "权利义务失衡。",
                "legal_bases": [],
                "possible_consequences": "条款可能被挑战。",
                "recommendation": "增加解除条件和责任。",
                "replacement_clause": "满足约定条件后方可解除。",
                "reasoning_summary": "同一条款从两个角度被识别。",
                "needs_manual_review": True,
            }

        payload = {
            "chunk_id": "C001",
            "analysis_status": "ok",
            "risks": [
                risk("合同权利义务", "单方解除权"),
                risk("期限与终止", "解除责任缺失"),
            ],
            "retrieval_trace": [],
        }
        result = aggregator.main(
            [json.dumps(payload, ensure_ascii=False)], "合同", "中国大陆"
        )
        report = json.loads(result["report_json"])
        self.assertEqual(report["risk_summary"]["counts"]["total"], 1)
        self.assertEqual(result["high_risk_count"], 1)
        self.assertIn("第2段", result["report_markdown"])
        self.assertNotIn("P?/¶", result["report_markdown"])
        self.assertNotIn('"report_meta"', result["report_markdown"])


if __name__ == "__main__":
    unittest.main()

