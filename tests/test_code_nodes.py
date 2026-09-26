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
kb_normalizer = load_module("03_normalize_kb_api_response.py")
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
    def normalize_single(self, confidence=0.9, location=None, chunk=None):
        excerpt = "乙方可以单方解除合同"
        raw = {
            "risks": [
                {
                    "risk_category": "合同权利义务",
                    "risk_level": "中",
                    "title": "单方解除风险",
                    "original_excerpt": excerpt,
                    "location": location or {},
                    "issue": "权利义务不对等。",
                    "legal_bases": [],
                    "possible_consequences": "条款可能被挑战。",
                    "recommendation": "增加解除条件。",
                    "replacement_clause": "满足约定条件后方可解除。",
                    "reasoning_summary": "单方解除权缺少限制。",
                    "needs_manual_review": False,
                    "confidence": confidence,
                }
            ]
        }
        chunk = chunk or {
            "chunk_id": "C001",
            "text": "[第1段] " + excerpt,
            "page_start": None,
            "page_end": None,
            "paragraph_start": 1,
            "paragraph_end": 1,
        }
        result = normalizer.main(
            json.dumps(raw, ensure_ascii=False), chunk, []
        )
        payload = json.loads(result["result_json"])
        return payload, payload["risks"][0]

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
        self.assertEqual(
            risk["location"]["locator_label"], "页码待定位·第1段"
        )
        self.assertEqual(
            risk["location"]["page_verification_status"], "待定位"
        )
        self.assertTrue(risk["needs_manual_review"], "all high risks need review")

    def test_uses_document_page_instead_of_model_page(self):
        chunk = {
            "chunk_id": "C002",
            "text": "乙方可以单方解除合同",
            "page_start": 2,
            "page_end": 2,
            "paragraph_start": 3,
            "paragraph_end": 3,
        }
        payload, risk = self.normalize_single(
            location={
                "page_start": 999,
                "page_end": 999,
                "paragraph_start": 3,
                "paragraph_end": 3,
            },
            chunk=chunk,
        )
        self.assertEqual(risk["location"]["page_start"], 2)
        self.assertEqual(risk["location"]["locator_label"], "第2页·第3段")
        self.assertTrue(risk["location"]["model_location_adjusted"])
        self.assertIn("模型页码与文档分段位置不一致", payload["analysis_warning"])

    def test_marks_page_pending_when_source_has_no_page(self):
        payload, risk = self.normalize_single(
            location={
                "page_start": 999,
                "page_end": 999,
                "paragraph_start": 1,
                "paragraph_end": 1,
            }
        )
        self.assertIsNone(risk["location"]["page_start"])
        self.assertNotIn("999", risk["location"]["locator_label"])
        self.assertIn("页码待定位", risk["location"]["locator_label"])
        self.assertIn("无法由文档分段确认", payload["analysis_warning"])

    def test_non_numeric_confidence_uses_default_without_crashing(self):
        payload, risk = self.normalize_single(confidence="很高")
        self.assertEqual(risk["confidence"], 0.5)
        self.assertIn("不是有效数字", risk["confidence_warning"])
        self.assertIn("置信度", payload["analysis_warning"])
        self.assertTrue(risk["needs_manual_review"])

    def test_empty_confidence_uses_default_and_records_warning(self):
        payload, risk = self.normalize_single(confidence="")
        self.assertEqual(risk["confidence"], 0.5)
        self.assertIn("未提供有效置信度", risk["confidence_warning"])
        self.assertIn("未提供有效置信度", payload["analysis_warning"])

    def test_none_confidence_uses_default_and_records_warning(self):
        payload, risk = self.normalize_single(confidence=None)
        self.assertEqual(risk["confidence"], 0.5)
        self.assertIn("未提供有效置信度", risk["confidence_warning"])
        self.assertTrue(payload["needs_manual_review"])

    def test_out_of_range_confidence_is_clamped(self):
        for value, expected, text in (
            (-0.25, 0.0, "低于 0"),
            (1.25, 1.0, "高于 1"),
        ):
            with self.subTest(value=value):
                _, risk = self.normalize_single(confidence=value)
                self.assertEqual(risk["confidence"], expected)
                self.assertIn(text, risk["confidence_warning"])

    def test_numeric_string_confidence_remains_valid(self):
        payload, risk = self.normalize_single(confidence="0.75")
        self.assertEqual(risk["confidence"], 0.75)
        self.assertEqual(risk["confidence_warning"], "")
        self.assertNotIn("置信度", payload["analysis_warning"])

    def test_non_finite_confidence_uses_default(self):
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                _, risk = self.normalize_single(confidence=value)
                self.assertEqual(risk["confidence"], 0.5)
                self.assertIn("不是有限数字", risk["confidence_warning"])

    def test_retrieval_failure_is_forwarded_to_chunk_result(self):
        excerpt = "乙方可以单方解除合同"
        raw = {
            "risks": [
                {
                    "risk_category": "合同权利义务",
                    "risk_level": "中",
                    "title": "单方解除风险",
                    "original_excerpt": excerpt,
                    "location": {},
                    "legal_bases": [],
                    "confidence": 0.8,
                }
            ]
        }
        chunk = {
            "chunk_id": "C001",
            "text": excerpt,
            "paragraph_start": 1,
            "paragraph_end": 1,
        }
        result = normalizer.main(
            json.dumps(raw, ensure_ascii=False),
            chunk,
            [],
            "timeout",
            "知识库检索请求超时（HTTP 504）",
        )
        payload = json.loads(result["result_json"])
        self.assertEqual(payload["retrieval_status"], "timeout")
        self.assertIn("请求超时", payload["analysis_warning"])
        self.assertTrue(payload["risks"][0]["needs_manual_review"])


class KnowledgeResponseTests(unittest.TestCase):
    def test_successful_retrieval_with_sources(self):
        body = {
            "records": [
                {
                    "score": 0.91,
                    "segment": {
                        "id": "segment-1",
                        "content": "第五百七十七条 当事人不履行合同义务的，应当承担违约责任。",
                        "document": {"name": "中华人民共和国民法典"},
                    },
                }
            ]
        }
        result = kb_normalizer.main(json.dumps(body, ensure_ascii=False), 200)
        self.assertEqual(result["retrieval_status"], "success")
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["retrieval_warning"], "")
        self.assertIn("中华人民共和国民法典", result["context_text"])

    def test_successful_retrieval_with_no_results_is_empty(self):
        result = kb_normalizer.main('{"records": []}', 200)
        self.assertEqual(result["retrieval_status"], "empty")
        self.assertEqual(result["source_count"], 0)
        self.assertIn("请求成功", result["context_text"])
        self.assertNotIn("接口报错", result["context_text"])

    def test_http_error_is_not_reported_as_empty(self):
        error_body = {
            "records": [
                {
                    "segment": {
                        "content": "错误响应中不应被信任的内容",
                        "document": {"name": "未知来源"},
                    }
                }
            ]
        }
        result = kb_normalizer.main(json.dumps(error_body), 500)
        self.assertEqual(result["retrieval_status"], "error")
        self.assertIn("HTTP 500", result["retrieval_warning"])
        self.assertIn("检索服务故障", result["context_text"])
        self.assertNotIn("请求成功", result["context_text"])
        self.assertEqual(result["source_count"], 0)

    def test_timeout_has_distinct_status(self):
        result = kb_normalizer.main(
            '{"error": "upstream request timed out"}', 504
        )
        self.assertEqual(result["retrieval_status"], "timeout")
        self.assertIn("请求超时", result["retrieval_warning"])
        self.assertIn("重试", result["context_text"])

    def test_malformed_success_response_is_an_error(self):
        result = kb_normalizer.main("not-json", 200)
        self.assertEqual(result["retrieval_status"], "error")
        self.assertIn("无法识别", result["retrieval_warning"])
        self.assertEqual(result["source_count"], 0)

    def test_embedded_http_status_takes_precedence(self):
        wrapped = {
            "status_code": 503,
            "body": json.dumps({"records": []}),
        }
        result = kb_normalizer.main(json.dumps(wrapped), 200)
        self.assertEqual(result["retrieval_status"], "error")
        self.assertEqual(result["http_status"], 503)


class AggregateTests(unittest.TestCase):
    @staticmethod
    def make_risk(
        level,
        paragraph,
        category="合同权利义务",
        title="单方解除风险",
        excerpt="甲方可随时解除合同且无需承担任何责任。",
        verified=False,
        confidence=0.6,
    ):
        return {
            "risk_category": category,
            "risk_level": level,
            "title": title,
            "original_excerpt": excerpt,
            "excerpt_verified": verified,
            "location": {
                "chunk_id": "C%03d" % paragraph,
                "page_start": None,
                "page_end": None,
                "page_verification_status": "待定位",
                "paragraph_start": paragraph,
                "paragraph_end": paragraph,
            },
            "issue": "权利义务失衡。",
            "legal_bases": (
                [
                    {
                        "title": "中华人民共和国民法典",
                        "article": "第四百九十七条",
                        "source_quote": "不合理地免除或者减轻其责任",
                        "verification_status": "retrieved_and_text_matched",
                    }
                ]
                if verified
                else []
            ),
            "possible_consequences": "条款可能被挑战。",
            "recommendation": "增加解除条件和责任。",
            "replacement_clause": "满足约定条件后方可解除。",
            "reasoning_summary": "同一条款可能被重复识别。",
            "needs_manual_review": level == "高",
            "uncertainty_reason": "",
            "confidence": confidence,
        }

    @staticmethod
    def aggregate(risks):
        payload = {
            "chunk_id": "C001",
            "analysis_status": "ok",
            "risks": risks,
            "retrieval_trace": [],
        }
        return aggregator.main(
            [json.dumps(payload, ensure_ascii=False)], "合同", "中国大陆"
        )

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

    def test_duplicate_level_conflict_keeps_later_high_risk(self):
        result = self.aggregate(
            [
                self.make_risk("低", 1, confidence=0.9),
                self.make_risk("高", 2, verified=True, confidence=0.8),
            ]
        )
        report = json.loads(result["report_json"])
        risk = report["risks"][0]
        self.assertEqual(report["risk_summary"]["counts"]["high"], 1)
        self.assertEqual(risk["risk_level"], "高")
        self.assertEqual(risk["risk_level_candidates"], ["高", "低"])
        self.assertTrue(risk["severity_conflict"])
        self.assertTrue(risk["needs_manual_review"])
        self.assertEqual(len(risk["related_locations"]), 2)
        self.assertIn("按较高等级保留", result["report_markdown"])

    def test_duplicate_level_conflict_keeps_earlier_high_risk(self):
        result = self.aggregate(
            [
                self.make_risk("高", 1, verified=True, confidence=0.8),
                self.make_risk("低", 2, confidence=0.95),
            ]
        )
        report = json.loads(result["report_json"])
        risk = report["risks"][0]
        self.assertEqual(risk["risk_level"], "高")
        self.assertTrue(risk["severity_conflict"])
        self.assertEqual(report["risk_summary"]["counts"]["low"], 0)

    def test_same_level_duplicate_prefers_better_evidence(self):
        weak = self.make_risk("中", 1, title="证据较弱", confidence=0.95)
        strong = self.make_risk(
            "中", 2, title="证据完整", verified=True, confidence=0.7
        )
        result = self.aggregate([weak, strong])
        risk = json.loads(result["report_json"])["risks"][0]
        self.assertEqual(risk["title"], "证据完整")
        self.assertFalse(risk["severity_conflict"])
        self.assertEqual(
            risk["legal_bases"][0]["verification_status"],
            "retrieved_and_text_matched",
        )

    def test_specialist_categories_with_same_excerpt_stay_separate(self):
        result = self.aggregate(
            [
                self.make_risk("高", 1, category="数据与隐私"),
                self.make_risk("高", 1, category="劳动用工"),
            ]
        )
        report = json.loads(result["report_json"])
        self.assertEqual(report["risk_summary"]["counts"]["total"], 2)

    def test_risk_sorting_and_report_exports_remain_available(self):
        risks = [
            self.make_risk("低", 1, excerpt="低风险条款"),
            self.make_risk("高", 2, excerpt="高风险条款"),
            self.make_risk("中", 3, excerpt="中风险条款"),
        ]
        result = self.aggregate(risks)
        report = json.loads(result["report_json"])
        self.assertEqual(
            [risk["risk_level"] for risk in report["risks"]],
            ["高", "中", "低"],
        )
        self.assertTrue(result["report_markdown"].startswith("# 文档法律与合规风险审查报告"))
        self.assertEqual(result["high_risk_count"], 1)
        self.assertTrue(result["manual_review_required"])

    def test_retrieval_failure_requires_review_even_without_risks(self):
        payload = {
            "chunk_id": "C001",
            "analysis_status": "ok",
            "analysis_warning": "知识库检索请求超时（HTTP 504）",
            "risks": [],
            "retrieval_trace": [],
            "needs_manual_review": True,
        }
        result = aggregator.main(
            [json.dumps(payload, ensure_ascii=False)], "合同", "中国大陆"
        )
        report = json.loads(result["report_json"])
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(len(report["processing_warnings"]), 1)
        self.assertIn("请求超时", result["report_markdown"])

    def test_document_wide_forum_fact_corrects_false_absence_claim(self):
        missing = self.make_risk(
            "中",
            1,
            category="争议解决",
            title="未约定适用法律、管辖法院或仲裁机构",
            excerpt="软件服务采购合同",
        )
        missing["issue"] = "合同未约定适用法律、管辖法院或仲裁机构。"
        missing["reasoning_summary"] = "合同缺少争议解决条款。"
        actual = self.make_risk(
            "中",
            8,
            category="争议解决",
            title="争议管辖仅约定乙方所在地人民法院",
            excerpt="双方发生争议时，仅可向乙方所在地人民法院起诉。",
        )

        result = self.aggregate([missing, actual])
        report = json.loads(result["report_json"])
        titles = [risk["title"] for risk in report["risks"]]

        self.assertTrue(report["document_fact_checks"]["forum_clause_detected"])
        self.assertIn("未明确约定合同适用法律及争议期间履行安排", titles)
        self.assertIn("争议管辖仅约定乙方所在地人民法院", titles)
        self.assertNotIn("未约定适用法律、管辖法院或仲裁机构", result["report_markdown"])
        self.assertIn("不认定为缺少管辖法院", result["report_markdown"])

    def test_false_forum_absence_only_risk_is_suppressed_when_clause_exists(self):
        missing = self.make_risk(
            "中",
            1,
            category="争议解决",
            title="未约定管辖法院",
            excerpt="软件服务采购合同",
        )
        missing["issue"] = "合同缺少争议解决和法院管辖条款。"
        actual = self.make_risk(
            "中",
            8,
            category="争议解决",
            title="乙方所在地法院条款对甲方不利",
            excerpt="双方发生争议时，仅可向乙方所在地人民法院起诉。",
        )

        report = json.loads(self.aggregate([missing, actual])["report_json"])
        self.assertEqual(report["risk_summary"]["counts"]["total"], 1)
        self.assertEqual(
            report["conflict_adjustments"][0]["action"],
            "suppressed_false_absence_risk",
        )

    def test_replacement_clause_blank_fields_become_visible_placeholders(self):
        risk = self.make_risk("中", 1)
        risk["replacement_clause"] = (
            "合同签署后日内支付合同总价的%；税率__%。"
            "统一社会信用代码：；住所：；授权代表：。"
        )
        result = self.aggregate([risk])
        clause = json.loads(result["report_json"])["risks"][0][
            "replacement_clause"
        ]
        self.assertNotIn("后日内", clause)
        self.assertNotIn("的%", clause)
        self.assertNotIn("__", clause)
        self.assertGreaterEqual(clause.count("【待填写】"), 6)

    def test_repeated_manual_review_reasons_are_collapsed(self):
        risk = self.make_risk("中", 1)
        risk["uncertainty_reason"] = (
            "知识库未返回可用法律依据，且合同未披露交易背景；"
            "至少一项法律依据未通过知识库文本匹配；"
            "知识库检索请求成功，但没有返回可用的匹配片段；"
            "合同未披露交易背景"
        )
        result = self.aggregate([risk])
        reason = json.loads(result["report_json"])["risks"][0][
            "uncertainty_reason"
        ]
        self.assertEqual(reason.count("知识库"), 1)
        self.assertEqual(reason.count("合同未披露交易背景"), 1)

    def test_identical_processing_warnings_are_grouped_by_chunks(self):
        message = "知识库检索请求成功，但没有返回可用的匹配片段"
        payloads = [
            json.dumps(
                {
                    "chunk_id": chunk,
                    "analysis_status": "ok",
                    "analysis_warning": message,
                    "risks": [],
                    "retrieval_trace": [],
                    "needs_manual_review": True,
                },
                ensure_ascii=False,
            )
            for chunk in ("C001", "C002")
        ]
        result = aggregator.main(payloads, "合同", "中国大陆")
        report = json.loads(result["report_json"])
        self.assertEqual(len(report["processing_warnings"]), 1)
        self.assertEqual(report["processing_warnings"][0]["chunk_ids"], ["C001", "C002"])
        self.assertEqual(result["report_markdown"].count(message), 1)


class PipelineCompatibilityTests(unittest.TestCase):
    def test_contract_review_pipeline_still_produces_report_outputs(self):
        document = (
            ROOT / "outputs" / "test_documents" / "01_合同测试_高风险.txt"
        ).read_text(encoding="utf-8")
        split_result = splitter.main(document)
        excerpt = "乙方可随时单方解除合同且无需退还任何费用"
        chunk = next(
            item for item in split_result["chunks"] if excerpt in item["text"]
        )
        raw = {
            "risks": [
                {
                    "risk_category": "期限与终止",
                    "risk_level": "高",
                    "title": "单方解除权失衡",
                    "original_excerpt": excerpt,
                    "location": {"page_start": 999},
                    "issue": "乙方享有无条件解除权。",
                    "legal_bases": [],
                    "possible_consequences": "甲方可能遭受费用损失。",
                    "recommendation": "设置明确解除条件和退款机制。",
                    "replacement_clause": "出现重大违约时方可解除并按比例退款。",
                    "reasoning_summary": "权利义务明显不对等。",
                    "confidence": 0.9,
                }
            ]
        }
        normalized = normalizer.main(
            json.dumps(raw, ensure_ascii=False), chunk, []
        )["result_json"]
        result = aggregator.main([normalized], "合同", "中国大陆")
        report = json.loads(result["report_json"])
        self.assertEqual(report["risk_summary"]["counts"]["high"], 1)
        self.assertIn("单方解除权失衡", result["report_markdown"])
        self.assertIn("页码待定位", result["report_markdown"])
        self.assertNotIn("第999页", result["report_markdown"])
        self.assertTrue(result["manual_review_required"])


if __name__ == "__main__":
    unittest.main()
