import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.services import persona_facts_service as pfs
from app.services import persona_gap_search as gap
from app.services.tavily_service import search_supplemental_material


@contextmanager
def _noop_lock(_character_id: int):
    yield


def _persona():
    return {field: "자료에서 확인되지 않음" for field in [
        "basic_info",
        "personality",
        "values_and_beliefs",
        "goals",
        "background",
        "worldview",
        "speech_style",
        "behavior_patterns",
        "summary",
    ]}


class _Response:
    def __init__(self, output_text):
        self.output_text = output_text


class _Interactions:
    def __init__(self, output_text):
        self.output_text = output_text
        self.calls = 0

    def create(self, model, input):
        self.calls += 1
        if isinstance(self.output_text, Exception):
            raise self.output_text
        return _Response(self.output_text)


class _Client:
    def __init__(self, interactions):
        self.interactions = interactions


DOCUMENTS = [
    {
        "id": 1,
        "display_order": 1,
        "url": "https://namu.wiki/w/한경(1984)",
        "title": "한경(1984)",
        "raw_text": "한경은 SUPER JUNIOR의 전 멤버다.",
    }
]


class GapPlanTests(unittest.TestCase):
    def test_summary_is_not_a_search_field(self):
        fields, reasons = gap.parse_insufficient_fields(
            {
                "insufficient_fields": ["summary", "goals", "not_a_field"],
                "reason": {"goals": "목적이 없다", "summary": "요약이 짧다"},
            }
        )
        self.assertEqual(fields, ["goals"])
        self.assertEqual(reasons, {"goals": "목적이 없다"})

    def test_many_fields_use_at_most_three_queries(self):
        fields = [
            "basic_info",
            "personality",
            "values_and_beliefs",
            "goals",
            "background",
            "worldview",
            "speech_style",
            "behavior_patterns",
        ]
        plans = gap.build_gap_search_plans("한경", DOCUMENTS, fields)
        self.assertEqual(len(plans), 3)
        self.assertEqual(
            [plan["fields"] for plan in plans],
            [
                ["basic_info", "personality", "values_and_beliefs"],
                ["goals", "background", "worldview"],
                ["speech_style", "behavior_patterns"],
            ],
        )
        self.assertIn("한경", plans[0]["query"])
        self.assertIn("한경(1984)", plans[0]["query"])
        self.assertIn("목표", plans[1]["query"])

    def test_partial_fields_search_only_those_fields(self):
        plans = gap.build_gap_search_plans("한경", DOCUMENTS, ["goals", "speech_style"])
        self.assertEqual([plan["fields"] for plan in plans], [["goals"], ["speech_style"]])
        self.assertNotIn("성격", plans[0]["query"])


class SufficiencyTests(unittest.TestCase):
    def _assess(self, output_text):
        interactions = _Interactions(output_text)
        with patch(
            "app.services.persona_gap_search._gemini_client",
            return_value=(_Client(interactions), "gemini-3.5-flash-lite"),
        ):
            return gap.assess_insufficient_fields("한경", []), interactions

    def test_all_sufficient_returns_no_fields(self):
        (fields, _reasons), _interactions = self._assess(
            json.dumps({"insufficient_fields": [], "reason": {}})
        )
        self.assertEqual(fields, [])

    def test_broken_json_raises_for_the_caller_to_skip_search(self):
        with self.assertRaises(ValueError):
            self._assess("판단할 수 없습니다.")


class SupplementalSearchTests(unittest.TestCase):
    @patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"})
    @patch("app.services.tavily_service.TavilyClient")
    def test_query_is_not_fixed_to_namu_name_search(self, client_cls):
        client_cls.return_value.search.return_value = {
            "results": [
                {
                    "title": "한경 인터뷰",
                    "url": "https://example.com/hangeng",
                    "raw_content": "한경의 목표는 솔로 활동이다.",
                },
                {
                    "title": "빈 문서",
                    "url": "https://example.com/empty",
                    "raw_content": "   ",
                    "content": "",
                },
            ]
        }
        hits = search_supplemental_material("한경 한경(1984) 목표와 목적")
        kwargs = client_cls.return_value.search.call_args.kwargs
        self.assertEqual(kwargs["query"], "한경 한경(1984) 목표와 목적")
        self.assertEqual(kwargs["max_results"], 5)
        self.assertNotIn("include_domains", kwargs)
        self.assertEqual(len(hits), 1)
        self.assertNotIn("검색URL:", hits[0]["raw_content"])


class _Conn:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_obj


class _Cursor:
    def __init__(self, existing_body=None):
        self.existing_body = existing_body
        self.inserts = []
        self._fetchone = None
        self._fetchall = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        if "SELECT cu.id, cu.url" in compact:
            self._fetchone = (7, "https://namu.wiki/w/한경(1984)")
            return
        if "SELECT csd.raw_text" in compact:
            self._fetchall = [(self.existing_body,)] if self.existing_body else []
            return
        if "INSERT INTO character_source_documents" in compact:
            self.inserts.append(params)
            self._fetchone = (21,)
            return
        raise AssertionError(compact)

    def fetchone(self):
        value = self._fetchone
        self._fetchone = None
        return value

    def fetchall(self):
        value = self._fetchall
        self._fetchall = []
        return value


class SupplementFlowTests(unittest.TestCase):
    def test_sufficient_fields_do_not_search(self):
        with (
            patch(
                "app.services.persona_facts_service.list_content_facts",
                return_value=[],
            ),
            patch.object(gap, "assess_insufficient_fields", return_value=([], {})),
            patch.object(gap, "search_supplemental_material") as search,
        ):
            saved = gap.supplement_persona_from_gaps(1, 2, "한경", DOCUMENTS)
        self.assertEqual(saved, 0)
        search.assert_not_called()

    def test_namesake_is_not_stored(self):
        cursor = _Cursor()
        namesake = {
            "title": "한경노 - 나무위키",
            "url": "https://namu.wiki/w/한경노",
            "raw_content": "정치인 한경노의 생애",
        }
        matched = {
            "title": "한경 인터뷰",
            "url": "https://example.com/hangeng",
            "raw_content": "SUPER JUNIOR 한경의 목표는 솔로 활동이다.",
        }

        def verify(_references, candidates):
            return [
                item
                for item in candidates
                if "한경노" not in item["raw_content"]
            ]

        with (
            patch(
                "app.services.persona_facts_service.list_content_facts",
                return_value=[],
            ),
            patch(
                "app.services.persona_facts_service.process_document_facts",
            ) as process,
            patch.object(
                gap,
                "assess_insufficient_fields",
                return_value=(["goals"], {"goals": "목적이 없다"}),
            ),
            patch.object(
                gap,
                "search_supplemental_material",
                return_value=[matched, namesake],
            ),
            patch.object(gap, "select_verified_search_hits", side_effect=verify),
            patch.object(
                gap,
                "select_field_relevant_hits",
                side_effect=lambda _name, _fields, items: items,
            ),
            patch.object(gap, "save_document_chunks") as chunks,
            patch.object(gap, "get_connection", return_value=_Conn(cursor)),
        ):
            saved = gap.supplement_persona_from_gaps(1, 2, "한경", DOCUMENTS)

        self.assertEqual(saved, 1)
        self.assertEqual(len(cursor.inserts), 1)
        anchor_id, title, body = cursor.inserts[0]
        self.assertEqual(anchor_id, 7)
        self.assertIn("검색URL: https://example.com/hangeng", title)
        self.assertNotIn("검색URL:", body)
        self.assertNotIn("한경노", body)
        chunks.assert_called_once_with(21)
        process.assert_called_once()
        self.assertEqual(process.call_args.args[2]["raw_text"], matched["raw_content"])

    def test_duplicate_body_is_not_inserted(self):
        cursor = _Cursor(existing_body="SUPER JUNIOR 한경의 목표는 솔로 활동이다.")
        hit = {
            "title": "한경 인터뷰",
            "url": "https://example.com/hangeng",
            "raw_content": "SUPER JUNIOR   한경의 목표는 솔로 활동이다.",
        }
        with (
            patch(
                "app.services.persona_facts_service.list_content_facts",
                return_value=[],
            ),
            patch("app.services.persona_facts_service.process_document_facts") as process,
            patch.object(
                gap,
                "assess_insufficient_fields",
                return_value=(["goals"], {}),
            ),
            patch.object(gap, "search_supplemental_material", return_value=[hit]),
            patch.object(gap, "select_verified_search_hits", side_effect=lambda _r, c: c),
            patch.object(
                gap,
                "select_field_relevant_hits",
                side_effect=lambda _name, _fields, items: items,
            ),
            patch.object(gap, "save_document_chunks") as chunks,
            patch.object(gap, "get_connection", return_value=_Conn(cursor)),
        ):
            saved = gap.supplement_persona_from_gaps(1, 2, "한경", DOCUMENTS)
        self.assertEqual(saved, 0)
        self.assertEqual(cursor.inserts, [])
        chunks.assert_not_called()
        process.assert_not_called()

    def test_search_failure_returns_without_saving(self):
        with (
            patch(
                "app.services.persona_facts_service.list_content_facts",
                return_value=[],
            ),
            patch.object(
                gap,
                "assess_insufficient_fields",
                return_value=(["goals"], {}),
            ),
            patch.object(
                gap,
                "search_supplemental_material",
                side_effect=RuntimeError("tavily down"),
            ),
            patch.object(gap, "save_supplemental_document") as save,
        ):
            saved = gap.supplement_persona_from_gaps(1, 2, "한경", DOCUMENTS)
        self.assertEqual(saved, 0)
        save.assert_not_called()

    def test_judgment_failure_skips_search(self):
        with (
            patch(
                "app.services.persona_facts_service.list_content_facts",
                return_value=[],
            ),
            patch.object(
                gap,
                "assess_insufficient_fields",
                side_effect=RuntimeError("gemini down"),
            ),
            patch.object(gap, "search_supplemental_material") as search,
        ):
            saved = gap.supplement_persona_from_gaps(1, 2, "한경", DOCUMENTS)
        self.assertEqual(saved, 0)
        search.assert_not_called()


class FieldRelevanceTests(unittest.TestCase):
    def test_personality_evidence_passes_and_biography_does_not(self):
        decisions = gap.parse_field_relevance(
            {
                "results": [
                    {
                        "id": 0,
                        "fields": ["personality"],
                        "reason": "쉽게 토라지고 감정을 바로 말로 드러낸다.",
                    },
                    {
                        "id": 1,
                        "fields": [],
                        "reason": "출생지와 학력, 데뷔 연도만 있다.",
                    },
                    {
                        "id": 2,
                        "fields": ["goals"],
                        "reason": "",
                    },
                ]
            },
            ["personality", "goals"],
        )
        self.assertEqual(decisions[0][0], ["personality"])
        self.assertEqual(decisions[1][0], [])
        self.assertEqual(decisions[2][0], ["goals"])
        self.assertEqual(decisions[2][1], "")

    def test_one_document_can_cover_several_missing_fields(self):
        decisions = gap.parse_field_relevance(
            {
                "results": [
                    {
                        "id": 0,
                        "fields": ["personality", "goals", "basic_info"],
                        "reason": "성급하고, 팀을 지키려 하며, 슈퍼주니어 멤버다.",
                    }
                ]
            },
            ["personality", "goals"],
        )
        self.assertEqual(decisions[0][0], ["personality", "goals"])

    def test_unrelated_field_name_is_ignored(self):
        decisions = gap.parse_field_relevance(
            {
                "results": [
                    {
                        "id": 0,
                        "fields": ["summary", "personality"],
                        "reason": "고집이 세다.",
                    }
                ]
            },
            ["personality"],
        )
        self.assertEqual(decisions[0][0], ["personality"])

    def _flow(self, hits, decisions, fields):
        cursor = _Cursor()
        search_calls = {"n": 0}

        def search(_query):
            search_calls["n"] += 1
            return hits

        def relevant(_name, requested, candidates):
            kept = []
            for index, candidate in enumerate(candidates):
                matched, reason = decisions.get(index, ([], ""))
                if matched and reason and set(matched) <= set(requested):
                    kept.append(candidate)
            return kept

        with (
            patch(
                "app.services.persona_facts_service.list_content_facts",
                return_value=[],
            ),
            patch("app.services.persona_facts_service.process_document_facts"),
            patch.object(
                gap,
                "assess_insufficient_fields",
                return_value=(fields, {}),
            ),
            patch.object(gap, "search_supplemental_material", side_effect=search),
            patch.object(gap, "select_verified_search_hits", side_effect=lambda _r, c: c),
            patch.object(gap, "select_field_relevant_hits", side_effect=relevant),
            patch.object(gap, "save_document_chunks") as chunks,
            patch.object(gap, "get_connection", return_value=_Conn(cursor)),
        ):
            saved = gap.supplement_persona_from_gaps(1, 2, "한경", DOCUMENTS)
        return saved, cursor, chunks, search_calls["n"]

    def test_biography_only_result_is_not_stored(self):
        saved, cursor, chunks, calls = self._flow(
            [
                {
                    "title": "한경 프로필",
                    "url": "https://example.com/profile",
                    "raw_content": "1984년 무단장시 출생. 대경대학교를 거쳐 2005년 데뷔했다.",
                }
            ],
            {0: ([], "출생지와 학력, 데뷔 이력만 있다.")},
            ["personality"],
        )
        self.assertEqual(saved, 0)
        self.assertEqual(cursor.inserts, [])
        chunks.assert_not_called()
        self.assertEqual(calls, 1)

    def test_same_person_without_goal_evidence_is_not_stored(self):
        saved, cursor, chunks, calls = self._flow(
            [
                {
                    "title": "한경 근황",
                    "url": "https://example.com/news",
                    "raw_content": "한경이 시상식에 참석했다.",
                }
            ],
            {0: ([], "구체적인 목표나 동기가 없다.")},
            ["goals"],
        )
        self.assertEqual(saved, 0)
        self.assertEqual(cursor.inserts, [])
        chunks.assert_not_called()
        self.assertEqual(calls, 1)

    def test_document_covering_multiple_fields_is_stored_once(self):
        saved, cursor, chunks, calls = self._flow(
            [
                {
                    "title": "한경 인터뷰",
                    "url": "https://example.com/interview",
                    "raw_content": "한경은 고집이 세고, 솔로로 오래 활동하는 것이 목표라고 말했다.",
                }
            ],
            {
                0: (
                    ["personality", "goals"],
                    "고집이 세다는 성격과 솔로 활동이라는 목표가 있다.",
                )
            },
            ["personality", "goals"],
        )
        self.assertEqual(saved, 1)
        self.assertEqual(len(cursor.inserts), 1)
        self.assertNotIn("검색URL:", cursor.inserts[0][2])
        chunks.assert_called_once_with(21)
        self.assertEqual(calls, 2)

    def test_no_relevant_document_does_not_add_tavily_calls(self):
        saved, _cursor, chunks, calls = self._flow(
            [
                {
                    "title": "한경 프로필",
                    "url": "https://example.com/profile",
                    "raw_content": "출생지와 학력만 있다.",
                },
                {
                    "title": "다른 기사",
                    "url": "https://example.com/other",
                    "raw_content": "시상식 참석 소식만 있다.",
                },
            ],
            {
                0: ([], "기본 정보뿐이다."),
                1: ([], "목표 근거가 없다."),
            },
            ["personality", "goals"],
        )
        self.assertEqual(saved, 0)
        chunks.assert_not_called()
        self.assertEqual(calls, 2)


class AnalysisHookTests(unittest.TestCase):
    def _run(self, supplement, facts):
        synthesized = []
        saved = []

        def synthesize(character_name, facts):
            synthesized.append((character_name, facts))
            persona = _persona()
            persona["goals"] = "사건을 해결한다"
            return persona

        def save(_character_id, persona, _model_name):
            saved.append(persona)
            return 77

        with (
            patch.object(pfs, "is_persona_confirmed", return_value=False),
            patch.object(pfs, "get_character_name", return_value="한경"),
            patch.object(pfs, "list_source_documents", return_value=DOCUMENTS),
            patch.object(pfs, "get_processed_document_ids", return_value=set()),
            patch.object(pfs, "adopt_identical_reextracted_document", return_value=False),
            patch.object(pfs, "process_document_facts"),
            patch.object(pfs, "list_content_facts", return_value=facts),
            patch.object(pfs, "synthesize_persona_from_facts", side_effect=synthesize),
            patch.object(pfs, "save_unconfirmed_persona", side_effect=save),
            patch.object(pfs, "supplement_persona_from_gaps", side_effect=supplement),
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        ):
            result = pfs.run_sequential_persona_analysis(4, 2)
        return result, synthesized, saved

    def test_supplemental_facts_reach_the_single_saved_persona(self):
        order = []
        facts = [
            {"id": 1, "field_name": "basic_info", "fact_text": "SUPER JUNIOR", "status": "active"},
            {"id": 2, "field_name": "goals", "fact_text": "사건을 해결한다", "status": "active"},
        ]

        def supplement(*_args):
            order.append("supplement")

        result, synthesized, saved = self._run(supplement, facts)
        order.append("done")
        self.assertEqual(order[0], "supplement")
        self.assertEqual(len(synthesized), 1)
        self.assertEqual(synthesized[0][1], facts)
        self.assertEqual(len(saved), 1)
        self.assertFalse(result["is_confirmed"])
        self.assertEqual(result["persona_id"], 77)

    def test_supplement_failure_still_saves_existing_facts(self):
        facts = [
            {"id": 1, "field_name": "basic_info", "fact_text": "SUPER JUNIOR", "status": "active"},
        ]

        def supplement(*_args):
            raise RuntimeError("추가 검색 실패")

        result, synthesized, saved = self._run(supplement, facts)
        self.assertEqual(len(synthesized), 1)
        self.assertEqual(synthesized[0][1], facts)
        self.assertEqual(len(saved), 1)
        self.assertFalse(result["is_confirmed"])

    def test_review_search_saves_without_reprocessing_documents(self):
        facts = [
            {"id": 1, "field_name": "basic_info", "fact_text": "SUPER JUNIOR", "status": "active"},
        ]

        def supplement(*_args):
            return 1

        with (
            patch.object(pfs, "is_persona_confirmed", return_value=False),
            patch.object(pfs, "get_character_name", return_value="한경"),
            patch.object(pfs, "list_source_documents", return_value=DOCUMENTS),
            patch.object(pfs, "process_document_facts") as process,
            patch.object(pfs, "list_content_facts", return_value=facts),
            patch.object(pfs, "synthesize_persona_from_facts", return_value=_persona()),
            patch.object(pfs, "save_unconfirmed_persona", return_value=77),
            patch.object(pfs, "supplement_persona_from_gaps", side_effect=supplement) as search,
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        ):
            result = pfs.run_supplemental_persona_search(4, 2)

        process.assert_not_called()
        search.assert_called_once()
        self.assertEqual(result["documents_saved"], 1)
        self.assertFalse(result["is_confirmed"])
        self.assertEqual(result["persona_id"], 77)

    def test_confirmed_persona_blocks_review_search(self):
        with (
            patch.object(pfs, "is_persona_confirmed", return_value=True),
            patch.object(pfs, "supplement_persona_from_gaps") as supplement,
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        ):
            with self.assertRaises(PermissionError):
                pfs.run_supplemental_persona_search(4, 2)
        supplement.assert_not_called()

    def test_confirmed_persona_blocks_before_supplement(self):
        with (
            patch.object(pfs, "is_persona_confirmed", return_value=True),
            patch.object(pfs, "supplement_persona_from_gaps") as supplement,
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        ):
            with self.assertRaises(PermissionError):
                pfs.run_sequential_persona_analysis(4, 2)
        supplement.assert_not_called()


if __name__ == "__main__":
    unittest.main()
