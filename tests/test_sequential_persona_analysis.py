"""다중 URL 순차 분석 단위 테스트.

Gemini와 Tavily, 로컬 PostgreSQL은 호출하지 않는다.
사실 반영은 가짜 커서로, 순차 흐름은 서비스 함수를 mock해서 확인한다.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.character_sources import extract_character_sources
from app.routers.personas import analyze_character_persona
from app.services import persona_facts_service as pfs
from app.services.character_analysis import (
    PERSONA_FIELDS,
    FactExtractionError,
    extract_document_fact_operations,
    extract_document_facts,
    parse_fact_candidate_response,
    parse_fact_operation_response,
    synthesize_persona_from_facts,
)


@contextmanager
def _noop_lock(_character_id: int):
    yield


def _docs(count: int) -> list[dict]:
    return [
        {
            "id": index,
            "display_order": index,
            "url": f"https://example.com/{index}",
            "title": f"문서{index}",
            "raw_text": f"본문 {index}",
        }
        for index in range(1, count + 1)
    ]


def _persona() -> dict:
    return {field: f"{field} 내용" for field in PERSONA_FIELDS}


class FakeCursor:
    """apply_fact_operations / mark_document_processed가 쓰는 SQL만 흉내 낸다."""

    def __init__(self):
        self.facts: dict[int, dict] = {}
        self._next_id = 1
        self._fetchone_queue = []
        self._fetchall_rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        sql_norm = " ".join(sql.split())
        self.rowcount = 0
        params = params or ()

        if "fact_compare_trace" in sql_norm and len(params) == 1:
            character_id = params[0]
            self._fetchall_rows = [
                (
                    fact["id"],
                    fact["field_name"],
                    fact["fact_text"],
                    list(fact["source_document_ids"]),
                    fact["status"],
                    fact["related_fact_id"],
                )
                for fact in self.facts.values()
                if fact["character_id"] == character_id
            ]
            return

        if sql_norm.startswith("SELECT fact_text, status") and len(params) == 2:
            fact_id, character_id = params
            fact = self.facts.get(int(fact_id))
            if fact is not None and fact["character_id"] == character_id:
                self._fetchone_queue.append((fact["fact_text"], fact["status"]))
            else:
                self._fetchone_queue.append(None)
            return

        if (
            sql_norm.startswith("SELECT 1")
            and "document_processed" not in sql_norm
            and len(params) == 2
        ):
            fact_id, character_id = params
            fact = self.facts.get(int(fact_id))
            owned = fact is not None and fact["character_id"] == character_id
            self._fetchone_queue.append((1,) if owned else None)
            return

        if sql_norm.startswith("SELECT 1") and len(params) == 4:
            document_id = params[3]
            exists = any(
                fact["status"] == pfs.STATUS_DOCUMENT_PROCESSED
                and document_id in fact["source_document_ids"]
                and fact["character_id"] == params[0]
                for fact in self.facts.values()
            )
            self._fetchone_queue.append((1,) if exists else None)
            return

        if sql_norm.startswith("INSERT INTO character_persona_facts"):
            fact_id = self._next_id
            self._next_id += 1
            self.facts[fact_id] = {
                "id": fact_id,
                "character_id": params[0],
                "field_name": params[1],
                "fact_text": params[2],
                "source_document_ids": list(params[3]),
                "status": params[4],
                "related_fact_id": params[5] if len(params) > 5 else None,
            }
            self.rowcount = 1
            return

        if sql_norm.startswith("UPDATE") and "fact_text = %s" in sql_norm:
            fact_text, source_ids, fact_id, character_id, status = params
            fact = self.facts.get(int(fact_id))
            if (
                fact
                and fact["character_id"] == character_id
                and fact["status"] == status
            ):
                fact["fact_text"] = fact_text
                fact["source_document_ids"] = sorted(
                    set(fact["source_document_ids"]) | set(source_ids)
                )
                self.rowcount = 1
            return

        if sql_norm.startswith("UPDATE") and "source_document_ids" in sql_norm:
            source_ids, fact_id, character_id = params[0], params[1], params[2]
            fact = self.facts.get(int(fact_id))
            if fact and fact["character_id"] == character_id:
                fact["source_document_ids"] = sorted(
                    set(fact["source_document_ids"]) | set(source_ids)
                )
                self.rowcount = 1

    def fetchone(self):
        if self._fetchone_queue:
            return self._fetchone_queue.pop(0)
        return None

    def fetchall(self):
        rows = self._fetchall_rows
        self._fetchall_rows = []
        return rows


class FactOperationTests(unittest.TestCase):
    def test_add_refine_duplicate_and_conflict(self):
        cur = FakeCursor()
        cur.facts[10] = {
            "id": 10,
            "character_id": 1,
            "field_name": "personality",
            "fact_text": "차분하다",
            "source_document_ids": [101],
            "status": pfs.STATUS_ACTIVE,
            "related_fact_id": None,
        }
        cur._next_id = 11

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=202,
            operations=[
                {
                    "action": "add",
                    "field_name": "goals",
                    "fact_text": "사건을 해결한다",
                    "existing_fact_id": None,
                },
                {
                    "action": "refine",
                    "field_name": "personality",
                    "fact_text": "차분하다. 신중하다.",
                    "existing_fact_id": 10,
                },
                {
                    "action": "duplicate",
                    "field_name": "personality",
                    "fact_text": "차분하다. 신중하다.",
                    "existing_fact_id": 10,
                },
                {
                    "action": "conflict",
                    "field_name": "personality",
                    "fact_text": "매우 급하다",
                    "existing_fact_id": 10,
                },
            ],
        )

        self.assertEqual(cur.facts[10]["fact_text"], "차분하다. 신중하다.")
        self.assertIn("차분하다", cur.facts[10]["fact_text"])
        self.assertEqual(cur.facts[10]["source_document_ids"], [101, 202])

        added = [fact for fact in cur.facts.values() if fact["field_name"] == "goals"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["status"], pfs.STATUS_ACTIVE)
        self.assertEqual(added[0]["source_document_ids"], [202])

        content_rows = [
            fact
            for fact in cur.facts.values()
            if fact["field_name"] == "personality"
        ]
        self.assertEqual(len(content_rows), 2)
        conflict = next(
            fact for fact in content_rows if fact["status"] == pfs.STATUS_CONFLICT
        )
        self.assertEqual(conflict["fact_text"], "매우 급하다")
        self.assertEqual(conflict["related_fact_id"], 10)
        self.assertEqual(cur.facts[10]["fact_text"], "차분하다. 신중하다.")

    def test_duplicate_does_not_insert_a_second_fact_row(self):
        cur = FakeCursor()
        cur.facts[3] = {
            "id": 3,
            "character_id": 1,
            "field_name": "background",
            "fact_text": "런던에 산다",
            "source_document_ids": [1],
            "status": pfs.STATUS_ACTIVE,
            "related_fact_id": None,
        }
        cur._next_id = 4

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=1,
            operations=[
                {
                    "action": "duplicate",
                    "field_name": "background",
                    "fact_text": "런던에 산다",
                    "existing_fact_id": 3,
                }
            ],
        )
        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=1,
            operations=[
                {
                    "action": "duplicate",
                    "field_name": "background",
                    "fact_text": "런던에 산다",
                    "existing_fact_id": 3,
                }
            ],
        )

        self.assertEqual(list(cur.facts), [3])
        self.assertEqual(cur.facts[3]["source_document_ids"], [1])

    def test_other_character_fact_id_is_not_mutated(self):
        cur = FakeCursor()
        cur.facts[50] = {
            "id": 50,
            "character_id": 99,
            "field_name": "personality",
            "fact_text": "다른 캐릭터의 사실",
            "source_document_ids": [1],
            "status": pfs.STATUS_ACTIVE,
            "related_fact_id": None,
        }
        cur._next_id = 51

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=8,
            operations=[
                {
                    "action": "refine",
                    "field_name": "personality",
                    "fact_text": "변조",
                    "existing_fact_id": 50,
                },
                {
                    "action": "duplicate",
                    "field_name": "personality",
                    "fact_text": "다른 캐릭터의 사실",
                    "existing_fact_id": 50,
                },
                {
                    "action": "conflict",
                    "field_name": "personality",
                    "fact_text": "상충",
                    "existing_fact_id": 50,
                },
            ],
        )

        self.assertEqual(cur.facts[50]["fact_text"], "다른 캐릭터의 사실")
        self.assertEqual(cur.facts[50]["source_document_ids"], [1])
        conflicts = [
            fact
            for fact in cur.facts.values()
            if fact["status"] == pfs.STATUS_CONFLICT
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["character_id"], 1)
        self.assertIsNone(conflicts[0]["related_fact_id"])

    def test_zero_fact_document_is_marked_processed_once(self):
        cur = FakeCursor()
        pfs.apply_fact_operations(cur, 1, 55, [])
        pfs.mark_document_processed(cur, 1, 55)
        pfs.mark_document_processed(cur, 1, 55)

        markers = [
            fact
            for fact in cur.facts.values()
            if fact["status"] == pfs.STATUS_DOCUMENT_PROCESSED
        ]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["field_name"], pfs.DOCUMENT_PROCESSED_FIELD)
        self.assertEqual(markers[0]["source_document_ids"], [55])
        self.assertEqual(markers[0]["fact_text"], "")


class FactPreservationTests(unittest.TestCase):
    def _fact(self, fact_id: int, text: str, sources: list[int]) -> dict:
        return {
            "id": fact_id,
            "character_id": 1,
            "field_name": "behavior_patterns",
            "fact_text": text,
            "source_document_ids": list(sources),
            "status": pfs.STATUS_ACTIVE,
            "related_fact_id": None,
        }

    def test_identical_fact_is_duplicate_and_only_adds_source(self):
        cur = FakeCursor()
        original = "런던 베이커가 221번지 B호에 사무실이 있다."
        cur.facts[2] = self._fact(2, original, [50])
        cur._next_id = 3

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "duplicate",
                    "field_name": "behavior_patterns",
                    "fact_text": original,
                    "existing_fact_id": 2,
                }
            ],
        )

        self.assertEqual(list(cur.facts), [2])
        self.assertEqual(cur.facts[2]["fact_text"], original)
        self.assertEqual(cur.facts[2]["source_document_ids"], [50, 51])

    def test_added_detail_refines_without_dropping_existing_text(self):
        cur = FakeCursor()
        original = "파이프 담배를 피운다."
        cur.facts[4] = self._fact(4, original, [50])
        cur._next_id = 5

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "refine",
                    "field_name": "behavior_patterns",
                    "fact_text": "파이프 담배를 피운다. 우울할 때 코카인을 사용한다.",
                    "existing_fact_id": 4,
                }
            ],
        )

        self.assertEqual(list(cur.facts), [4])
        self.assertIn("파이프 담배를 피운다", cur.facts[4]["fact_text"])
        self.assertIn("코카인을 사용한다", cur.facts[4]["fact_text"])
        self.assertEqual(cur.facts[4]["source_document_ids"], [50, 51])

    def test_refine_that_omits_old_details_keeps_them(self):
        cur = FakeCursor()
        original = (
            "겉모습으로 출생지와 직업을 추리한다. "
            "리볼버 사격 연습을 한다. "
            "양봉에 소질이 있다."
        )
        cur.facts[4] = self._fact(4, original, [50])
        cur._next_id = 5

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "refine",
                    "field_name": "behavior_patterns",
                    "fact_text": "파이프 담배를 피운다. 바이올린을 연주한다.",
                    "existing_fact_id": 4,
                }
            ],
        )

        self.assertEqual(cur.facts[4]["fact_text"], original)
        self.assertEqual(cur.facts[4]["source_document_ids"], [50])
        for detail in ("겉모습으로 출생지와 직업을 추리한다", "리볼버 사격 연습을 한다", "양봉에 소질이 있다"):
            self.assertIn(detail, cur.facts[4]["fact_text"])
        added = [fact for fact in cur.facts.values() if fact["id"] != 4]
        self.assertEqual(len(added), 1)
        self.assertIn("파이프 담배를 피운다", added[0]["fact_text"])
        self.assertIn("바이올린을 연주한다", added[0]["fact_text"])
        self.assertNotIn("리볼버", added[0]["fact_text"])
        self.assertEqual(added[0]["source_document_ids"], [51])

    def test_duplicate_with_new_information_does_not_drop_it(self):
        cur = FakeCursor()
        original = "직업은 자문 탐정이다."
        cur.facts[1] = self._fact(1, original, [50])
        cur.facts[1]["field_name"] = "basic_info"
        cur._next_id = 2

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "duplicate",
                    "field_name": "basic_info",
                    "fact_text": "아서 코난 도일이 창조했고 1887년 주홍색 연구에 처음 등장한 자문 탐정이다.",
                    "existing_fact_id": 1,
                }
            ],
        )

        self.assertEqual(cur.facts[1]["fact_text"], original)
        self.assertEqual(cur.facts[1]["source_document_ids"], [50])
        added = [fact for fact in cur.facts.values() if fact["id"] != 1]
        self.assertEqual(len(added), 1)
        self.assertIn("1887년", added[0]["fact_text"])
        self.assertEqual(added[0]["source_document_ids"], [51])
        self.assertEqual(added[0]["status"], pfs.STATUS_ACTIVE)

    def test_independent_information_is_added(self):
        cur = FakeCursor()
        cur.facts[6] = self._fact(6, "친형 마이크로프트 홈즈가 있다.", [50])
        cur.facts[6]["field_name"] = "background"
        cur._next_id = 7

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "add",
                    "field_name": "background",
                    "fact_text": "숙적은 제임스 모리아티 교수다.",
                    "existing_fact_id": None,
                }
            ],
        )

        self.assertEqual(cur.facts[6]["fact_text"], "친형 마이크로프트 홈즈가 있다.")
        self.assertEqual(cur.facts[6]["source_document_ids"], [50])
        added = [fact for fact in cur.facts.values() if fact["id"] != 6]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["fact_text"], "숙적은 제임스 모리아티 교수다.")
        self.assertEqual(added[0]["source_document_ids"], [51])

    def test_conflict_keeps_original_fact(self):
        cur = FakeCursor()
        original = "1854년부터 1957년까지 활동한 것으로 적혀 있다."
        cur.facts[1] = self._fact(1, original, [50])
        cur.facts[1]["field_name"] = "basic_info"
        cur._next_id = 2

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "conflict",
                    "field_name": "basic_info",
                    "fact_text": "사망 연도는 확인되지 않는다.",
                    "existing_fact_id": 1,
                }
            ],
        )

        self.assertEqual(cur.facts[1]["fact_text"], original)
        self.assertEqual(cur.facts[1]["source_document_ids"], [50])
        conflicts = [
            fact
            for fact in cur.facts.values()
            if fact["status"] == pfs.STATUS_CONFLICT
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["related_fact_id"], 1)
        self.assertEqual(conflicts[0]["source_document_ids"], [51])

    def test_partial_support_does_not_add_source_to_whole_fact(self):
        cur = FakeCursor()
        original = (
            "연애는 이성과 공존할 수 없다고 생각한다. "
            "불가능한 것을 제외하면 남는 것이 진실이다. "
            "하늘 아래 새로운 것은 없다고 생각한다."
        )
        cur.facts[5] = self._fact(5, original, [50])
        cur.facts[5]["field_name"] = "values_and_beliefs"
        cur._next_id = 6

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "duplicate",
                    "field_name": "values_and_beliefs",
                    "fact_text": "불가능한 것을 제외하면 남는 것이 진실이다.",
                    "existing_fact_id": 5,
                }
            ],
        )

        self.assertEqual(list(cur.facts), [5])
        self.assertEqual(cur.facts[5]["fact_text"], original)
        self.assertEqual(cur.facts[5]["source_document_ids"], [50])

    def test_other_character_fact_is_not_modified(self):
        cur = FakeCursor()
        cur.facts[50] = {
            "id": 50,
            "character_id": 99,
            "field_name": "behavior_patterns",
            "fact_text": "다른 캐릭터는 양봉을 한다.",
            "source_document_ids": [1],
            "status": pfs.STATUS_ACTIVE,
            "related_fact_id": None,
        }
        cur._next_id = 51

        pfs.apply_fact_operations(
            cur,
            character_id=1,
            document_id=51,
            operations=[
                {
                    "action": "refine",
                    "field_name": "behavior_patterns",
                    "fact_text": "다른 캐릭터는 양봉을 한다. 바이올린을 연주한다.",
                    "existing_fact_id": 50,
                },
                {
                    "action": "duplicate",
                    "field_name": "behavior_patterns",
                    "fact_text": "다른 캐릭터는 양봉을 한다.",
                    "existing_fact_id": 50,
                },
            ],
        )

        self.assertEqual(cur.facts[50]["fact_text"], "다른 캐릭터는 양봉을 한다.")
        self.assertEqual(cur.facts[50]["source_document_ids"], [1])
        self.assertEqual(list(cur.facts), [50])


class PromptTests(unittest.TestCase):
    def _fake_client(self, captured: dict, payload: str):
        class FakeResponse:
            output_text = payload

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            class interactions:
                @staticmethod
                def create(model, input):
                    captured["prompt"] = input
                    captured["model"] = model
                    return FakeResponse()

        return FakeClient

    def test_fact_prompt_includes_character_name_and_forbids_invention(self):
        captured = {}
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.services.character_analysis.genai.Client",
            self._fake_client(captured, '{"operations":[]}'),
        ):
            operations = extract_document_fact_operations(
                character_name="셜록 홈즈",
                raw_text="셜록 홈즈는 베이커가에 산다.",
                existing_facts=[
                    {
                        "id": 1,
                        "field_name": "basic_info",
                        "fact_text": "탐정이다",
                        "source_document_ids": [1],
                        "status": "active",
                        "related_fact_id": None,
                    }
                ],
                title="문서1",
                url="https://example.com/1",
            )

        self.assertEqual(operations, [])
        prompt = captured["prompt"]
        self.assertIn("셜록 홈즈", prompt)
        self.assertIn("누적된 기존 정보", prompt)
        self.assertIn("탐정이다", prompt)
        self.assertIn("만들어내지 마세요", prompt)
        self.assertIn("conflict", prompt)
        self.assertIn("duplicate", prompt)
        self.assertIn("양봉", prompt)
        self.assertIn("일부만 뒷받침", prompt)
        self.assertIn("일부만 겹친다", prompt)
        self.assertIn("주변 인물과의 관계", prompt)

    def test_synthesis_uses_only_accumulated_facts(self):
        captured = {}
        payload = "{" + ",".join(f'"{field}":"정리"' for field in PERSONA_FIELDS) + "}"
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.services.character_analysis.genai.Client",
            self._fake_client(captured, payload),
        ):
            persona = synthesize_persona_from_facts(
                "셜록 홈즈",
                [
                    {
                        "id": 1,
                        "field_name": "personality",
                        "fact_text": "냉정하다",
                        "status": "active",
                        "source_document_ids": [1],
                        "related_fact_id": None,
                    }
                ],
            )

        self.assertEqual(set(persona), set(PERSONA_FIELDS))
        self.assertIn("셜록 홈즈", captured["prompt"])
        self.assertIn("냉정하다", captured["prompt"])
        self.assertIn("확인되지 않은 사실은 만들어내지 마세요", captured["prompt"])


class SequentialFlowTests(unittest.TestCase):
    def _run(self, documents, processed=None, adopt_ids=None, fail_on=None):
        processed = set(processed or [])
        adopt_ids = set(adopt_ids or [])
        calls = []
        passed_facts = []

        def process(_character_id, _name, document):
            if fail_on is not None and document["id"] == fail_on:
                raise RuntimeError("문서 분석 실패")
            calls.append(document["id"])
            passed_facts.append(document["id"])

        def adopt(_character_id, document_id):
            return document_id in adopt_ids

        persona = _persona()
        with (
            patch.object(pfs, "is_persona_confirmed", return_value=False),
            patch.object(pfs, "get_character_name", return_value="셜록 홈즈"),
            patch.object(pfs, "list_source_documents", return_value=documents),
            patch.object(pfs, "get_processed_document_ids", return_value=set(processed)),
            patch.object(pfs, "adopt_identical_reextracted_document", side_effect=adopt),
            patch.object(pfs, "process_document_facts", side_effect=process),
            patch.object(pfs, "list_content_facts", return_value=[{"id": 1}]),
            patch.object(pfs, "synthesize_persona_from_facts", return_value=persona) as synthesize,
            patch.object(pfs, "save_unconfirmed_persona", return_value=77),
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        ):
            result = pfs.run_sequential_persona_analysis(1, 2)

        return result, calls, synthesize

    def test_one_two_and_three_urls_run_in_display_order(self):
        for count in (1, 2, 3):
            with self.subTest(url_count=count):
                result, calls, synthesize = self._run(_docs(count))
                self.assertEqual(calls, list(range(1, count + 1)))
                self.assertEqual(result["persona_id"], 77)
                self.assertFalse(result["is_confirmed"])
                self.assertEqual(result["documents_processed"], count)
                synthesize.assert_called_once()

    def test_accumulated_facts_are_loaded_before_each_document(self):
        seen = []

        def process(character_id, character_name, document):
            seen.append((character_id, character_name, document["display_order"]))

        with (
            patch.object(pfs, "is_persona_confirmed", return_value=False),
            patch.object(pfs, "get_character_name", return_value="왓슨"),
            patch.object(pfs, "list_source_documents", return_value=_docs(2)),
            patch.object(pfs, "get_processed_document_ids", return_value=set()),
            patch.object(pfs, "adopt_identical_reextracted_document", return_value=False),
            patch.object(pfs, "process_document_facts", side_effect=process),
            patch.object(pfs, "list_content_facts", return_value=[]),
            patch.object(pfs, "synthesize_persona_from_facts", return_value=_persona()),
            patch.object(pfs, "save_unconfirmed_persona", return_value=3),
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        ):
            pfs.run_sequential_persona_analysis(4, 5)

        self.assertEqual(seen, [(4, "왓슨", 1), (4, "왓슨", 2)])

    def test_extraction_does_not_receive_existing_facts(self):
        facts = [{"id": 9, "field_name": "goals", "fact_text": "정의를 추구한다", "status": "active"}]
        captured = {}

        def extract(**kwargs):
            captured.update(kwargs)
            return []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self):
                return FakeCursor()

        with (
            patch.object(pfs, "list_content_facts", return_value=facts),
            patch.object(pfs, "extract_document_facts", side_effect=extract),
            patch.object(pfs, "get_connection", return_value=Conn()),
        ):
            pfs.process_document_facts(
                1,
                "셜록 홈즈",
                _docs(1)[0],
            )

        self.assertEqual(captured["character_name"], "셜록 홈즈")
        self.assertEqual(captured["document_body"], "본문 1")
        self.assertEqual(captured["document_title"], "문서1")
        self.assertNotIn("existing_facts", captured)

    def test_retry_skips_successful_document_and_skips_persona_on_failure(self):
        state = {"processed": set(), "fail": True}
        synthesized = []

        def process(_character_id, _name, document):
            if document["id"] == 2 and state["fail"]:
                raise RuntimeError("두 번째 문서 실패")
            state["processed"].add(document["id"])

        def processed_ids(_character_id):
            return set(state["processed"])

        patches = (
            patch.object(pfs, "is_persona_confirmed", return_value=False),
            patch.object(pfs, "get_character_name", return_value="셜록 홈즈"),
            patch.object(pfs, "list_source_documents", return_value=_docs(3)),
            patch.object(pfs, "get_processed_document_ids", side_effect=processed_ids),
            patch.object(pfs, "adopt_identical_reextracted_document", return_value=False),
            patch.object(pfs, "process_document_facts", side_effect=process),
            patch.object(pfs, "list_content_facts", return_value=[]),
            patch.object(
                pfs,
                "synthesize_persona_from_facts",
                side_effect=lambda **kwargs: synthesized.append(kwargs) or _persona(),
            ),
            patch.object(pfs, "save_unconfirmed_persona", return_value=8),
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
            with self.assertRaises(RuntimeError):
                pfs.run_sequential_persona_analysis(1, 2)

        self.assertEqual(state["processed"], {1})
        self.assertEqual(synthesized, [])

        state["fail"] = False
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
            result = pfs.run_sequential_persona_analysis(1, 2)

        self.assertEqual(state["processed"], {1, 2, 3})
        self.assertEqual(len(synthesized), 1)
        self.assertEqual(result["documents_skipped"], 1)
        self.assertEqual(result["documents_processed"], 2)
        self.assertFalse(result["is_confirmed"])

    def test_identical_reextract_skips_gemini(self):
        result, calls, synthesize = self._run(_docs(2), adopt_ids={2})
        self.assertEqual(calls, [1])
        self.assertEqual(result["documents_skipped"], 1)
        synthesize.assert_called_once()

    def test_confirmed_persona_blocks_analysis(self):
        with (
            patch.object(pfs, "is_persona_confirmed", return_value=True),
            patch.object(pfs, "process_document_facts") as process,
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        ):
            with self.assertRaises(PermissionError):
                pfs.run_sequential_persona_analysis(1, 2)
        process.assert_not_called()

        with patch(
            "app.routers.personas.run_sequential_persona_analysis",
            side_effect=PermissionError("confirmed_persona"),
        ):
            with self.assertRaises(HTTPException) as caught:
                analyze_character_persona(1, 2)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("확정된 페르소나", caught.exception.detail)

    def test_concurrent_analysis_returns_409(self):
        with patch(
            "app.routers.personas.run_sequential_persona_analysis",
            side_effect=pfs.AnalysisInProgressError("analysis_in_progress"),
        ):
            with self.assertRaises(HTTPException) as caught:
                analyze_character_persona(1, 2)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("이미 진행 중", caught.exception.detail)

    def test_advisory_lock_failure_raises_before_analysis(self):
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                self.sql = sql

            def fetchone(self):
                return (False,)

        class Conn:
            def cursor(self):
                return Cursor()

            def close(self):
                self.closed = True

        conn = Conn()
        with (
            patch.object(pfs, "get_connection", return_value=conn),
            patch.object(pfs, "process_document_facts") as process,
        ):
            with self.assertRaises(pfs.AnalysisInProgressError):
                with pfs.character_analysis_lock(12):
                    process()
        process.assert_not_called()
        self.assertTrue(conn.closed)


class ExtractSkipTests(unittest.TestCase):
    def test_identical_url_and_body_is_not_inserted_again(self):
        class Cursor:
            def __init__(self, rows):
                self.rows = list(rows)
                self.inserts = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params=None):
                self.sql = " ".join(sql.split())
                if "INSERT INTO character_source_documents" in self.sql:
                    self.inserts.append(params)
                    self.row = None
                    return
                self.row = self.rows.pop(0)

            def fetchone(self):
                return self.row

            def fetchall(self):
                rows = self.row
                self.row = None
                return rows

        class Conn:
            def __init__(self, cursor):
                self.cursor_obj = cursor

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self):
                return self.cursor_obj

        lookup_cursor = Cursor([[(4, "https://example.com/a")]])
        loop_cursor = Cursor([(4,), (15,)])
        count_cursor = Cursor([(2,)])

        def connections():
            yield Conn(lookup_cursor)
            yield Conn(loop_cursor)
            yield Conn(count_cursor)

        source = connections()
        stdout = io.StringIO()

        with (
            redirect_stdout(stdout),
            patch(
                "app.routers.character_sources.extract_urls",
                return_value={
                    "results": [
                        {
                            "url": "https://example.com/a",
                            "title": "A",
                            "raw_content": "같은 본문",
                        }
                    ]
                },
            ),
            patch(
                "app.routers.character_sources.get_connection",
                side_effect=lambda: next(source),
            ),
        ):
            result = extract_character_sources(3, 9)

        self.assertEqual(result["saved_count"], 0)
        self.assertEqual(result["character_id"], 3)
        self.assertEqual(loop_cursor.inserts, [])
        self.assertIn("이미 저장", result["message"])
        self.assertIn("Tavily 요청 URL: ['https://example.com/a']", stdout.getvalue())


class FactExtractionReliabilityTests(unittest.TestCase):
    def test_explicit_empty_operations_are_accepted(self):
        self.assertEqual(
            parse_fact_operation_response(
                '{"operations":[]}',
                status="completed",
            ),
            [],
        )

    def test_valid_add_operation_is_kept(self):
        operations = parse_fact_operation_response(
            '{"operations":[{"action":"add","field_name":"background",'
            '"fact_text":"왓슨과 221B에서 함께 산다.","existing_fact_id":null}]}',
            status="completed",
        )
        self.assertEqual(operations[0]["action"], "add")
        self.assertIn("왓슨", operations[0]["fact_text"])

    def test_empty_response_is_failure(self):
        with self.assertRaises(FactExtractionError):
            parse_fact_operation_response("   ", status="completed")

    def test_invalid_json_is_failure(self):
        with self.assertRaises(FactExtractionError):
            parse_fact_operation_response('{"operations":[', status="completed")

    def test_missing_or_wrong_schema_is_failure(self):
        with self.assertRaises(FactExtractionError):
            parse_fact_operation_response('{"facts":[]}', status="completed")
        with self.assertRaises(FactExtractionError):
            parse_fact_operation_response('{"operations":{}}', status="completed")
        with self.assertRaises(FactExtractionError):
            parse_fact_operation_response(
                '{"operations":[{"action":"unknown","field_name":"background","fact_text":"x"}]}',
                status="completed",
            )

    def test_incomplete_status_is_failure(self):
        with self.assertRaises(FactExtractionError):
            parse_fact_operation_response(
                '{"operations":[]}',
                status="requires_continuation",
            )

    def test_empty_operations_mark_document_processed(self):
        cursor = FakeCursor()

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self):
                return cursor

        with (
            patch.object(pfs, "list_content_facts", return_value=[]),
            patch.object(pfs, "extract_document_facts", return_value=[]),
            patch.object(pfs, "get_connection", return_value=Conn()),
        ):
            pfs.process_document_facts(38, "셜록 홈즈", {"id": 52, "raw_text": "본문"})

        markers = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == pfs.STATUS_DOCUMENT_PROCESSED
        ]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["source_document_ids"], [52])

    def test_failed_extraction_does_not_mark_or_block_retry(self):
        state = {"processed": {50, 51}, "fail": True}
        seen = []

        def process(_character_id, _name, document):
            seen.append(document["id"])
            if document["id"] == 52 and state["fail"]:
                raise FactExtractionError("응답이 잘렸습니다.")
            state["processed"].add(document["id"])

        documents = [
            {"id": 50, "display_order": 1, "url": "a", "title": "a", "raw_text": "a"},
            {"id": 51, "display_order": 2, "url": "b", "title": "b", "raw_text": "b"},
            {"id": 52, "display_order": 3, "url": "c", "title": "c", "raw_text": "c"},
        ]
        patches = (
            patch.object(pfs, "is_persona_confirmed", return_value=False),
            patch.object(pfs, "get_character_name", return_value="셜록 홈즈"),
            patch.object(pfs, "list_source_documents", return_value=documents),
            patch.object(
                pfs,
                "get_processed_document_ids",
                side_effect=lambda _character_id: set(state["processed"]),
            ),
            patch.object(pfs, "adopt_identical_reextracted_document", return_value=False),
            patch.object(pfs, "process_document_facts", side_effect=process),
            patch.object(pfs, "list_content_facts", return_value=[{"id": 1}]),
            patch.object(pfs, "synthesize_persona_from_facts", return_value=_persona()),
            patch.object(pfs, "save_unconfirmed_persona", return_value=84),
            patch.object(pfs, "character_analysis_lock", _noop_lock),
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
            with self.assertRaises(FactExtractionError):
                pfs.run_sequential_persona_analysis(38, 32)
        self.assertEqual(seen, [52])
        self.assertEqual(state["processed"], {50, 51})

        state["fail"] = False
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
            pfs.run_sequential_persona_analysis(38, 32)
        self.assertEqual(seen, [52, 52])
        self.assertEqual(state["processed"], {50, 51, 52})

    def test_prompt_does_not_drop_new_facts_because_of_overlap(self):
        captured = {}

        class FakeResponse:
            output_text = '{"operations":[]}'
            status = "completed"

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            class interactions:
                @staticmethod
                def create(model, input):
                    captured["prompt"] = input
                    return FakeResponse()

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.services.character_analysis.genai.Client",
            FakeClient,
        ):
            extract_document_fact_operations(
                character_name="셜록 홈즈",
                raw_text="홈즈는 스탬퍼드의 소개로 왓슨과 함께 살게 된다.",
                existing_facts=[
                    {
                        "id": 2,
                        "field_name": "background",
                        "fact_text": "탐정사무실은 런던 베이커가 221번지 B호에 있다.",
                        "source_document_ids": [50],
                        "status": "active",
                        "related_fact_id": None,
                    }
                ],
            )

        prompt = captured["prompt"]
        self.assertIn("탐정사무실은 런던 베이커가", prompt)
        self.assertIn("스탬퍼드", prompt)
        self.assertIn("새 정보는 추출하세요", prompt)
        self.assertNotIn("기존 사실이 있으면 새 정보는 생략", prompt)


class FactGranularityPromptTests(unittest.TestCase):
    def _prompt(self, raw_text: str, existing_facts: list[dict] | None = None) -> str:
        captured = {}

        class FakeResponse:
            output_text = '{"operations":[]}'
            status = "completed"

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            class interactions:
                @staticmethod
                def create(model, input):
                    captured["prompt"] = input
                    return FakeResponse()

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.services.character_analysis.genai.Client",
            FakeClient,
        ):
            extract_document_fact_operations(
                character_name="셜록 홈즈",
                raw_text=raw_text,
                existing_facts=existing_facts
                or [
                    {
                        "id": 2,
                        "field_name": "background",
                        "fact_text": "탐정사무실은 런던 베이커가 221번지 B호에 있다.",
                        "source_document_ids": [50],
                        "status": "active",
                        "related_fact_id": None,
                    }
                ],
            )
        return captured["prompt"]

    def test_introduction_and_meeting_are_separate_facts(self):
        prompt = self._prompt(
            "스탬퍼드가 왓슨에게 홈즈를 소개해 두 사람의 만남을 주선했다. "
            "홈즈와 왓슨은 이후 함께 살게 되었다."
        )
        self.assertIn("스탬퍼드가 왓슨에게 홈즈를 소개", prompt)
        self.assertIn("만남을 주선한 인물", prompt)
        self.assertIn("만남의 경위와 동거 사실은 한 문장으로 합치지 마세요", prompt)
        self.assertIn("첫 만남을 주선했다.", prompt)
        self.assertIn("함께 살게 되었다.", prompt)

    def test_publication_year_and_first_appearance_are_requested(self):
        prompt = self._prompt(
            "이 소설은 1887년에 출판되었고, 셜록 홈즈가 처음 등장한다."
        )
        self.assertIn("1887년에 출판", prompt)
        self.assertIn("작품 발표 연도", prompt)
        self.assertIn("첫 등장 시점", prompt)
        self.assertIn("작품의 출판 정보와 캐릭터의 개인 정보를 구분", prompt)

    def test_case_outcome_is_kept_apart_from_another_life(self):
        prompt = self._prompt(
            "홈즈는 마부로 위장한 제퍼슨 호프를 체포했다. "
            "제퍼슨 호프는 1847년 유타에서 루시와 자랐다."
        )
        self.assertIn("제퍼슨 호프를 체포했다", prompt)
        self.assertIn("주요 사건의 발생 과정과 결과", prompt)
        self.assertIn("과거 생애 전체는 분석 대상의 사실로 저장하지 마세요", prompt)
        self.assertIn("함께한 사건과 그 결과는 add", prompt)

    def test_cohabitation_is_not_merged_with_how_they_met(self):
        prompt = self._prompt(
            "스탬퍼드의 소개로 홈즈와 왓슨이 만났고, 둘은 221B에서 함께 살았다."
        )
        self.assertIn("하나의 핵심 정보만", prompt)
        self.assertIn("대표 문장 하나로 뭉뚱그리지 마세요", prompt)
        self.assertIn("만남의 경위와 동거 사실은 한 문장으로 합치지 마세요", prompt)

    def test_work_feature_is_not_rewritten_as_character_habit(self):
        prompt = self._prompt(
            "이 소설은 돋보기를 수사 도구로 도입한 최초의 탐정소설이다."
        )
        self.assertIn("돋보기를 수사 도구로 도입", prompt)
        self.assertIn("작품의 서술이나 특징을 캐릭터의 습관이나 행동으로 바꾸지 마세요", prompt)
        self.assertIn("캐릭터가 그 도구를 사용한다는 사실로 옮기지 마세요", prompt)

    def test_partial_overlap_still_requests_the_new_detail(self):
        prompt = self._prompt(
            "홈즈는 이미 쓰던 베이커가 221B에서 1881년부터 왓슨과 함께 살았다."
        )
        self.assertIn("탐정사무실은 런던 베이커가 221번지 B호에 있다.", prompt)
        self.assertIn("1881년부터 왓슨과 함께 살았다", prompt)
        self.assertIn("일부만 겹친다는 이유로 새 정보를 통째로 생략하지 마세요", prompt)
        self.assertIn("기존 사실에 이미 있는 정보와 새로 확인한 정보를 구분", prompt)


class FactCandidateClassificationTests(unittest.TestCase):
    def _existing(self, fact_id: int, text: str, field_name: str = "background") -> dict:
        return {
            "id": fact_id,
            "character_id": 38,
            "field_name": field_name,
            "fact_text": text,
            "source_document_ids": [50],
            "status": "active",
            "related_fact_id": None,
        }

    def _process(self, existing_rows: list[dict], candidates: list[dict]) -> FakeCursor:
        cursor = FakeCursor()
        for row in existing_rows:
            cursor.facts[row["id"]] = dict(row)
        if cursor.facts:
            cursor._next_id = max(cursor.facts) + 1

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def cursor(self):
                return cursor

        listed = [
            {
                "id": row["id"],
                "field_name": row["field_name"],
                "fact_text": row["fact_text"],
                "source_document_ids": list(row["source_document_ids"]),
                "status": row["status"],
                "related_fact_id": row["related_fact_id"],
            }
            for row in existing_rows
        ]
        with (
            patch.object(pfs, "list_content_facts", return_value=listed),
            patch.object(pfs, "extract_document_facts", return_value=candidates),
            patch.object(pfs, "get_connection", return_value=Conn()),
        ):
            pfs.process_document_facts(
                38,
                "셜록 홈즈",
                {
                    "id": 52,
                    "title": "A Study in Scarlet",
                    "url": "https://example.com/scarlet",
                    "raw_text": "원문",
                },
            )
        return cursor

    def test_new_fact_is_added(self):
        cursor = self._process(
            [],
            [{"field_name": "background", "fact_text": "스탬퍼드가 왓슨과 홈즈의 만남을 주선했다."}],
        )
        added = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == "active"
        ]
        self.assertEqual(len(added), 1)
        self.assertIn("스탬퍼드", added[0]["fact_text"])
        self.assertEqual(added[0]["source_document_ids"], [52])

    def test_same_fact_is_duplicate(self):
        original = "1881년에 왓슨과 함께 살게 되었다."
        cursor = self._process(
            [self._existing(12, original)],
            [{"field_name": "background", "fact_text": original}],
        )
        self.assertEqual(cursor.facts[12]["fact_text"], original)
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50, 52])
        self.assertEqual(
            [fact for fact in cursor.facts.values() if fact["status"] == "active"],
            [cursor.facts[12]],
        )

    def test_superset_fact_is_refine(self):
        cursor = self._process(
            [self._existing(12, "왓슨과 함께 산다.")],
            [{"field_name": "background", "fact_text": "왓슨과 함께 산다. 1881년부터이다."}],
        )
        self.assertEqual(
            cursor.facts[12]["fact_text"],
            "왓슨과 함께 산다. 1881년부터이다.",
        )
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50, 52])
        active = [
            fact for fact in cursor.facts.values() if fact["status"] == "active"
        ]
        self.assertEqual(active, [cursor.facts[12]])

    def test_partial_overlap_keeps_existing_fact(self):
        original = "왓슨과 함께 산다. 파이프를 피운다."
        cursor = self._process(
            [self._existing(4, original, "behavior_patterns")],
            [{"field_name": "behavior_patterns", "fact_text": "왓슨과 함께 산다. 스탬퍼드가 만남을 주선했다."}],
        )
        self.assertEqual(cursor.facts[4]["fact_text"], original)
        self.assertEqual(cursor.facts[4]["source_document_ids"], [50])
        added = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == "active" and fact["id"] != 4
        ]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["fact_text"], "스탬퍼드가 만남을 주선했다.")
        self.assertEqual(added[0]["source_document_ids"], [52])

    def test_independent_facts_are_stored_separately(self):
        cursor = self._process(
            [],
            [
                {"field_name": "background", "fact_text": "1887년에 처음 등장했다."},
                {"field_name": "background", "fact_text": "제퍼슨 호프를 체포했다."},
            ],
        )
        texts = [
            fact["fact_text"]
            for fact in cursor.facts.values()
            if fact["status"] == "active"
        ]
        self.assertEqual(texts, ["1887년에 처음 등장했다.", "제퍼슨 호프를 체포했다."])

    def test_candidates_in_the_same_document_are_not_duplicated(self):
        text = "스탬퍼드가 왓슨과 홈즈의 만남을 주선했다."
        cursor = self._process(
            [],
            [
                {"field_name": "background", "fact_text": text},
                {"field_name": "background", "fact_text": text},
            ],
        )
        active = [
            fact for fact in cursor.facts.values() if fact["status"] == "active"
        ]
        self.assertEqual(len(active), 1)

    def test_failed_extraction_does_not_mark_document(self):
        def connect():
            raise AssertionError("실패 응답은 저장 트랜잭션을 열지 않아야 합니다.")

        with (
            patch.object(pfs, "list_content_facts", return_value=[]),
            patch.object(
                pfs,
                "extract_document_facts",
                side_effect=FactExtractionError("JSON 파싱 실패"),
            ),
            patch.object(pfs, "get_connection", side_effect=connect),
        ):
            with self.assertRaises(FactExtractionError):
                pfs.process_document_facts(
                    38,
                    "셜록 홈즈",
                    {"id": 52, "raw_text": "원문"},
                )

    def test_exact_sentence_is_duplicate_and_source_is_unique(self):
        original = "홈즈는 왓슨과 함께 살았다."
        cursor = self._process(
            [self._existing(12, original)],
            [
                {"field_name": "background", "fact_text": original},
                {"field_name": "background", "fact_text": f"  {original}  "},
            ],
        )
        self.assertEqual(cursor.facts[12]["fact_text"], original)
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50, 52])
        active = [fact for fact in cursor.facts.values() if fact["status"] == "active"]
        self.assertEqual(active, [cursor.facts[12]])

    def test_paraphrase_is_not_forced_into_duplicate(self):
        operations = pfs.classify_fact_candidates(
            [{"id": 2, "field_name": "background", "fact_text": "홈즈는 왓슨과 함께 살았다.", "status": "active"}],
            [{"field_name": "background", "fact_text": "홈즈는 왓슨과 같이 거주했다."}],
        )
        self.assertEqual(operations[0]["action"], "add")
        self.assertIsNone(operations[0]["existing_fact_id"])

    def test_more_specific_job_refines_without_dropping_old_sentence(self):
        original = "홈즈는 탐정이다."
        cursor = self._process(
            [self._existing(1, original, "basic_info")],
            [{"field_name": "basic_info", "fact_text": "홈즈의 직업은 상담 탐정이다."}],
        )
        self.assertEqual(
            cursor.facts[1]["fact_text"],
            "홈즈는 탐정이다. 홈즈의 직업은 상담 탐정이다.",
        )
        self.assertEqual(cursor.facts[1]["status"], "active")
        self.assertEqual(cursor.facts[1]["source_document_ids"], [50, 52])
        self.assertEqual(cursor.facts[1]["source_document_ids"].count(52), 1)
        active = [fact for fact in cursor.facts.values() if fact["status"] == "active"]
        self.assertEqual(active, [cursor.facts[1]])

    def test_less_specific_cohabitation_does_not_replace_richer_fact(self):
        original = "홈즈는 1881년 221B 베이커 가에서 왓슨을 만나 함께 살게 됐다."
        incoming = "홈즈는 왓슨과 221B의 방을 나누어 살았다."
        cursor = self._process(
            [self._existing(12, original)],
            [{"field_name": "background", "fact_text": incoming}],
        )
        self.assertEqual(cursor.facts[12]["fact_text"], original)
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50])
        added = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == "active" and fact["id"] != 12
        ]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["fact_text"], incoming)
        self.assertEqual(added[0]["source_document_ids"], [52])

    def test_different_event_is_added(self):
        original = "홈즈는 왓슨과 함께 살았다."
        incoming = "홈즈는 제퍼슨 호프를 체포했다."
        cursor = self._process(
            [self._existing(12, original)],
            [{"field_name": "background", "fact_text": incoming}],
        )
        self.assertEqual(cursor.facts[12]["fact_text"], original)
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50])
        added = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == "active" and fact["id"] != 12
        ]
        self.assertEqual(added[0]["fact_text"], incoming)

    def test_clear_year_conflict_keeps_original_row(self):
        original = "1854년에 태어났다."
        incoming = "1860년에 태어났다."
        cursor = self._process(
            [self._existing(1, original, "basic_info")],
            [{"field_name": "basic_info", "fact_text": incoming}],
        )
        self.assertEqual(cursor.facts[1]["fact_text"], original)
        self.assertEqual(cursor.facts[1]["status"], "active")
        self.assertEqual(cursor.facts[1]["source_document_ids"], [50])
        self.assertIsNone(cursor.facts[1]["related_fact_id"])
        conflicts = [
            fact for fact in cursor.facts.values() if fact["status"] == "conflict"
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["fact_text"], incoming)
        self.assertEqual(conflicts[0]["related_fact_id"], 1)
        self.assertEqual(conflicts[0]["source_document_ids"], [52])
        self.assertEqual(conflicts[0]["field_name"], "basic_info")

    def test_different_subject_is_not_merged(self):
        operations = pfs.classify_fact_candidates(
            [{"id": 1, "field_name": "basic_info", "fact_text": "홈즈는 탐정이다.", "status": "active"}],
            [{"field_name": "basic_info", "fact_text": "왓슨은 의사이다."}],
        )
        self.assertEqual(operations[0]["action"], "add")
        self.assertIsNone(operations[0]["existing_fact_id"])

    def test_repeated_refine_in_one_document_keeps_one_source(self):
        cursor = self._process(
            [self._existing(12, "왓슨과 함께 산다.")],
            [
                {"field_name": "background", "fact_text": "왓슨과 함께 산다. 1881년부터이다."},
                {
                    "field_name": "background",
                    "fact_text": "왓슨과 함께 산다. 1881년부터이다. 베이커가에서 지냈다.",
                },
            ],
        )
        self.assertEqual(
            cursor.facts[12]["fact_text"],
            "왓슨과 함께 산다. 1881년부터이다. 베이커가에서 지냈다.",
        )
        self.assertEqual(cursor.facts[12]["status"], "active")
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50, 52])
        self.assertEqual(cursor.facts[12]["source_document_ids"].count(52), 1)
        active = [fact for fact in cursor.facts.values() if fact["status"] == "active"]
        self.assertEqual(active, [cursor.facts[12]])

    def test_duplicate_does_not_repeat_an_existing_source(self):
        original = "홈즈는 왓슨과 함께 살았다."
        existing = self._existing(12, original)
        existing["source_document_ids"] = [50, 52]
        cursor = self._process(
            [existing],
            [
                {"field_name": "background", "fact_text": original},
                {"field_name": "background", "fact_text": original},
            ],
        )
        self.assertEqual(cursor.facts[12]["fact_text"], original)
        self.assertEqual(cursor.facts[12]["status"], "active")
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50, 52])
        active = [fact for fact in cursor.facts.values() if fact["status"] == "active"]
        self.assertEqual(active, [cursor.facts[12]])

    def test_paraphrase_is_stored_as_separate_row(self):
        original = "홈즈는 왓슨과 함께 살았다."
        incoming = "홈즈는 왓슨과 같이 거주했다."
        cursor = self._process(
            [self._existing(12, original)],
            [{"field_name": "background", "fact_text": incoming}],
        )
        self.assertEqual(cursor.facts[12]["fact_text"], original)
        self.assertEqual(cursor.facts[12]["status"], "active")
        self.assertEqual(cursor.facts[12]["source_document_ids"], [50])
        added = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == "active" and fact["id"] != 12
        ]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["fact_text"], incoming)
        self.assertEqual(added[0]["source_document_ids"], [52])

    def test_different_subject_is_stored_as_separate_row(self):
        original = "홈즈는 탐정이다."
        incoming = "왓슨은 의사이다."
        cursor = self._process(
            [self._existing(1, original, "basic_info")],
            [{"field_name": "basic_info", "fact_text": incoming}],
        )
        self.assertEqual(cursor.facts[1]["fact_text"], original)
        self.assertEqual(cursor.facts[1]["status"], "active")
        self.assertEqual(cursor.facts[1]["source_document_ids"], [50])
        added = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == "active" and fact["id"] != 1
        ]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["fact_text"], incoming)
        self.assertEqual(added[0]["source_document_ids"], [52])

    def test_repeated_conflict_candidate_is_stored_once(self):
        original = "1854년에 태어났다."
        incoming = "1860년에 태어났다."
        cursor = self._process(
            [self._existing(1, original, "basic_info")],
            [
                {"field_name": "basic_info", "fact_text": incoming},
                {"field_name": "basic_info", "fact_text": incoming},
            ],
        )
        self.assertEqual(cursor.facts[1]["fact_text"], original)
        self.assertEqual(cursor.facts[1]["status"], "active")
        self.assertEqual(cursor.facts[1]["source_document_ids"], [50])
        conflicts = [
            fact for fact in cursor.facts.values() if fact["status"] == "conflict"
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["fact_text"], incoming)
        self.assertEqual(conflicts[0]["related_fact_id"], 1)
        self.assertEqual(conflicts[0]["source_document_ids"], [52])

    def test_existing_conflict_row_is_left_unchanged(self):
        original = "1860년에 태어났다."
        existing = self._existing(7, original, "basic_info")
        existing["status"] = "conflict"
        existing["related_fact_id"] = 1
        existing["source_document_ids"] = [51]
        cursor = self._process(
            [existing],
            [{"field_name": "basic_info", "fact_text": original}],
        )
        self.assertEqual(cursor.facts[7]["fact_text"], original)
        self.assertEqual(cursor.facts[7]["status"], "conflict")
        self.assertEqual(cursor.facts[7]["source_document_ids"], [51])
        self.assertEqual(cursor.facts[7]["related_fact_id"], 1)
        content = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] in ("active", "conflict")
        ]
        self.assertEqual(content, [cursor.facts[7]])

    def test_different_field_is_not_merged(self):
        operations = pfs.classify_fact_candidates(
            [{"id": 1, "field_name": "basic_info", "fact_text": "홈즈는 탐정이다.", "status": "active"}],
            [{"field_name": "background", "fact_text": "홈즈는 탐정이다."}],
        )
        self.assertEqual(operations[0]["action"], "add")

    def test_different_field_is_stored_without_changing_existing_row(self):
        original = "홈즈는 탐정이다."
        cursor = self._process(
            [self._existing(1, original, "basic_info")],
            [{"field_name": "background", "fact_text": original}],
        )
        self.assertEqual(cursor.facts[1]["fact_text"], original)
        self.assertEqual(cursor.facts[1]["status"], "active")
        self.assertEqual(cursor.facts[1]["source_document_ids"], [50])
        added = [
            fact
            for fact in cursor.facts.values()
            if fact["status"] == "active" and fact["id"] != 1
        ]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["field_name"], "background")
        self.assertEqual(added[0]["fact_text"], original)
        self.assertEqual(added[0]["source_document_ids"], [52])

    def test_candidate_parser_accepts_only_explicit_empty_list(self):
        self.assertEqual(
            parse_fact_candidate_response('{"facts":[]}', status="completed"),
            [],
        )
        with self.assertRaises(FactExtractionError):
            parse_fact_candidate_response("", status="completed")
        with self.assertRaises(FactExtractionError):
            parse_fact_candidate_response('{"facts":', status="completed")
        with self.assertRaises(FactExtractionError):
            parse_fact_candidate_response('{"operations":[]}', status="completed")

    def test_extract_prompt_omits_existing_facts(self):
        captured = {}

        class FakeResponse:
            output_text = '{"facts":[]}'
            status = "completed"

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            class interactions:
                @staticmethod
                def create(model, input):
                    captured["prompt"] = input
                    return FakeResponse()

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "app.services.character_analysis.genai.Client",
            FakeClient,
        ):
            facts = extract_document_facts(
                character_name="셜록 홈즈",
                document_title="A Study in Scarlet",
                document_url="https://example.com/scarlet",
                document_body="스탬퍼드가 소개했고, 1887년에 처음 등장했다.",
            )

        self.assertEqual(facts, [])
        prompt = captured["prompt"]
        self.assertIn("스탬퍼드가 소개했고", prompt)
        self.assertIn("중복되는지 판단하지 않는다", prompt)
        self.assertIn("하나의 핵심 사실만", prompt)
        self.assertIn("다른 인물의 생애", prompt)
        self.assertIn("개인적 습관이나 성격으로 바꾸지 않는다", prompt)
        self.assertNotIn("[누적된 기존 정보]", prompt)
        self.assertIn("만남을 주선하거나 연결한 인물", prompt)
        self.assertIn("처음 만나게 된 경위", prompt)
        self.assertIn("만남을 주선한 인물과 실제 만남을 구분한다", prompt)
        self.assertIn("작품의 발표 연도", prompt)
        self.assertIn("작품 내 첫 등장 시점", prompt)
        self.assertIn("출판 정보라는 이유만으로", prompt)
        self.assertIn("주요 사건과 사건의 진행 및 결과", prompt)
        self.assertIn("basic_info: 출생, 신분, 직업", prompt)
        self.assertIn("background: 과거, 인간관계", prompt)
        self.assertIn("behavior_patterns: 반복적인 습관", prompt)
        self.assertIn("가장 적절한 필드 하나만 선택한다", prompt)
        self.assertIn("스탬퍼드의 소개로 존 왓슨과 처음 만나게 되었다.", prompt)
        self.assertIn("존 왓슨과 221B 베이커 가에서 함께 살게 되었다.", prompt)
        self.assertIn("1887년 발표된 작품에서 처음 등장했다.", prompt)

    def test_save_error_does_not_mark_document_processed(self):
        marked = []
        exited = []

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                exited.append(exc_type)
                return False

            def cursor(self):
                cursor = FakeCursor()
                cursor.__enter__ = lambda: cursor
                return cursor

        with (
            patch.object(pfs, "list_content_facts", return_value=[]),
            patch.object(
                pfs,
                "extract_document_facts",
                return_value=[{"field_name": "background", "fact_text": "새 사실이다."}],
            ),
            patch.object(pfs, "apply_fact_operations", side_effect=RuntimeError("저장 실패")),
            patch.object(
                pfs,
                "mark_document_processed",
                side_effect=lambda *args: marked.append(args),
            ),
            patch.object(pfs, "get_connection", return_value=Conn()),
        ):
            with self.assertRaises(RuntimeError):
                pfs.process_document_facts(
                    38,
                    "셜록 홈즈",
                    {"id": 52, "raw_text": "원문"},
                )
        self.assertEqual(marked, [])
        self.assertEqual(exited, [RuntimeError])


class FactCompareTraceTests(unittest.TestCase):
    def test_trace_is_not_written_without_env(self):
        env = os.environ.copy()
        env.pop("FACT_COMPARE_TRACE_PATH", None)
        with patch.dict(os.environ, env, clear=True), patch("builtins.open", side_effect=AssertionError("trace")):
            FactCandidateClassificationTests()._process(
                [],
                [{"field_name": "background", "fact_text": "새 사실이다."}],
            )

    def test_trace_records_three_stages_and_only_affected_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fact-compare.jsonl")
            env = os.environ.copy()
            env["FACT_COMPARE_TRACE_PATH"] = path
            with patch.dict(os.environ, env, clear=True):
                FactCandidateClassificationTests()._process(
                    [
                        {
                            "id": 1,
                            "character_id": 38,
                            "field_name": "background",
                            "fact_text": "김단비는 서울에서 자랐다.",
                            "source_document_ids": [50],
                            "status": "active",
                            "related_fact_id": None,
                        },
                        {
                            "id": 3,
                            "character_id": 38,
                            "field_name": "basic_info",
                            "fact_text": "김단비는 교사이다.",
                            "source_document_ids": [50],
                            "status": "active",
                            "related_fact_id": None,
                        },
                        {
                            "id": 9,
                            "character_id": 38,
                            "field_name": "background",
                            "fact_text": "바뀌지 않는 사실이다.",
                            "source_document_ids": [50],
                            "status": "active",
                            "related_fact_id": None,
                        },
                    ],
                    [
                        {"field_name": "background", "fact_text": "김단비는 서울에서 자랐다."},
                        {"field_name": "basic_info", "fact_text": "김단비의 직업은 음악 교사이다."},
                    ],
                )
            stages = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]

        self.assertEqual([item["stage"] for item in stages], ["candidates", "operations", "stored"])
        self.assertEqual(
            stages[0]["items"],
            [
                {"field_name": "background", "fact_text": "김단비는 서울에서 자랐다."},
                {"field_name": "basic_info", "fact_text": "김단비의 직업은 음악 교사이다."},
            ],
        )
        self.assertEqual(
            [(item["action"], item["existing_fact_id"]) for item in stages[1]["items"]],
            [("duplicate", 1), ("refine", 3)],
        )
        stored_ids = [item["id"] for item in stages[2]["items"]]
        self.assertIn(1, stored_ids)
        self.assertIn(3, stored_ids)
        self.assertNotIn(9, stored_ids)
        self.assertTrue(any(item["status"] == "document_processed" for item in stages[2]["items"]))
        refined = next(item for item in stages[2]["items"] if item["id"] == 3)
        self.assertEqual(
            refined["fact_text"],
            "김단비는 교사이다. 김단비의 직업은 음악 교사이다.",
        )

    def test_trace_write_failure_still_saves_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["FACT_COMPARE_TRACE_PATH"] = directory
            with patch.dict(os.environ, env, clear=True):
                cursor = FactCandidateClassificationTests()._process(
                    [],
                    [{"field_name": "background", "fact_text": "새 사실이다."}],
                )
        self.assertTrue(any(fact["status"] == "active" for fact in cursor.facts.values()))
        self.assertTrue(
            any(fact["status"] == "document_processed" for fact in cursor.facts.values())
        )


class MigrationFileTests(unittest.TestCase):
    def test_sql_adds_only_character_persona_facts(self):
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "002_character_persona_facts.sql"
        sql = sql_path.read_text(encoding="utf-8")
        self.assertEqual(sql.lower().count("create table"), 1)
        self.assertIn("CREATE TABLE character_persona_facts", sql)
        for column in (
            "id",
            "character_id",
            "field_name",
            "fact_text",
            "source_document_ids",
            "status",
            "related_fact_id",
            "created_at",
            "updated_at",
        ):
            self.assertIn(column, sql)
        self.assertIn("document_processed", sql)
        self.assertNotIn("CREATE TABLE character_source_documents", sql)
        self.assertNotIn("CREATE TABLE character_personas", sql)


if __name__ == "__main__":
    unittest.main()
