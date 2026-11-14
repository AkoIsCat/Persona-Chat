"""
Unit tests for sequential multi-URL persona fact accumulation.

These tests mock Gemini and DB where needed so they can run without
live Postgres / API keys.
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.services import persona_facts_service as pfs
from app.services.character_analysis import (
    PERSONA_FIELDS,
    analyze_character,
    extract_document_fact_operations,
)


@contextmanager
def _noop_lock(_character_id: int):
    yield


class FakeCursor:
    def __init__(self):
        self.executed = []
        self._fetchone_queue = []
        self._next_id = 1
        self.facts = {}  # id -> dict
        self.rowcount = 0

    def queue_fetchone(self, value):
        self._fetchone_queue.append(value)

    def execute(self, sql, params=None):
        sql_norm = " ".join(sql.split())
        self.executed.append((sql_norm, params))
        self.rowcount = 0

        if (
            "SELECT 1 FROM character_persona_facts" in sql_norm
            and "AND character_id = %s" in sql_norm
            and "document_processed" not in sql_norm
            and len(params) == 2
        ):
            fact_id, character_id = params
            fact = self.facts.get(int(fact_id))
            ok = fact is not None and fact["character_id"] == character_id
            self._fetchone_queue.append((1,) if ok else None)
            return

        if "SELECT 1 FROM character_persona_facts" in sql_norm and "document_processed" in str(
            params
        ):
            document_id = params[3]
            exists = any(
                f["status"] == pfs.STATUS_DOCUMENT_PROCESSED
                and document_id in f["source_document_ids"]
                for f in self.facts.values()
            )
            self._fetchone_queue.append((1,) if exists else None)
            return

        if sql_norm.startswith("INSERT INTO character_persona_facts"):
            field_name = params[1]
            fact_text = params[2]
            source_ids = list(params[3])
            status = params[4]
            related = params[5] if len(params) > 5 else None
            fact_id = self._next_id
            self._next_id += 1
            self.facts[fact_id] = {
                "id": fact_id,
                "character_id": params[0],
                "field_name": field_name,
                "fact_text": fact_text,
                "source_document_ids": source_ids,
                "status": status,
                "related_fact_id": related,
            }
            self.rowcount = 1
            return

        if sql_norm.startswith("UPDATE character_persona_facts") and "fact_text = %s" in sql_norm:
            fact_text, source_arr, fact_id, character_id, status = params
            fact = self.facts.get(int(fact_id))
            if (
                fact
                and fact["character_id"] == character_id
                and fact["status"] == status
            ):
                fact["fact_text"] = fact_text
                merged = sorted(set(fact["source_document_ids"] + list(source_arr)))
                fact["source_document_ids"] = merged
                self.rowcount = 1
            return

        if sql_norm.startswith("UPDATE character_persona_facts") and "source_document_ids" in sql_norm:
            # duplicate path: (source_arr, fact_id, character_id, active, conflict)
            source_arr, fact_id, character_id = params[0], params[1], params[2]
            fact = self.facts.get(int(fact_id))
            if fact and fact["character_id"] == character_id:
                merged = sorted(set(fact["source_document_ids"] + list(source_arr)))
                fact["source_document_ids"] = merged
                self.rowcount = 1
            return

    def fetchone(self):
        if self._fetchone_queue:
            return self._fetchone_queue.pop(0)
        return None


class ApplyFactOperationsTests(unittest.TestCase):
    def test_add_refine_duplicate_conflict(self):
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

        operations = [
            {
                "action": "add",
                "field_name": "goals",
                "fact_text": "세계를 지킨다",
                "existing_fact_id": None,
            },
            {
                "action": "refine",
                "field_name": "personality",
                "fact_text": "차분하고 신중하다",
                "existing_fact_id": 10,
            },
            {
                "action": "duplicate",
                "field_name": "personality",
                "fact_text": "차분하고 신중하다",
                "existing_fact_id": 10,
            },
            {
                "action": "conflict",
                "field_name": "personality",
                "fact_text": "매우 급하다",
                "existing_fact_id": 10,
            },
        ]

        pfs.apply_fact_operations(cur, character_id=1, document_id=202, operations=operations)

        self.assertEqual(cur.facts[10]["fact_text"], "차분하고 신중하다")
        self.assertEqual(cur.facts[10]["source_document_ids"], [101, 202])

        added = [f for f in cur.facts.values() if f["field_name"] == "goals"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["source_document_ids"], [202])
        self.assertEqual(added[0]["status"], pfs.STATUS_ACTIVE)

        conflicts = [
            f
            for f in cur.facts.values()
            if f["status"] == pfs.STATUS_CONFLICT
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["fact_text"], "매우 급하다")
        self.assertEqual(conflicts[0]["related_fact_id"], 10)
        self.assertEqual(cur.facts[10]["fact_text"], "차분하고 신중하다")

    def test_duplicate_and_refine_ignore_other_character_fact_id(self):
        cur = FakeCursor()
        # Fact owned by character 99 — must not be mutated by character 1 ops
        cur.facts[50] = {
            "id": 50,
            "character_id": 99,
            "field_name": "personality",
            "fact_text": "다른 캐릭터 사실",
            "source_document_ids": [1],
            "status": pfs.STATUS_ACTIVE,
            "related_fact_id": None,
        }
        cur._next_id = 100

        operations = [
            {
                "action": "duplicate",
                "field_name": "personality",
                "fact_text": "다른 캐릭터 사실",
                "existing_fact_id": 50,
            },
            {
                "action": "refine",
                "field_name": "personality",
                "fact_text": "변조 시도",
                "existing_fact_id": 50,
            },
            {
                "action": "conflict",
                "field_name": "personality",
                "fact_text": "상충 시도",
                "existing_fact_id": 50,
            },
        ]

        pfs.apply_fact_operations(cur, character_id=1, document_id=202, operations=operations)

        # Other character's fact untouched
        self.assertEqual(cur.facts[50]["fact_text"], "다른 캐릭터 사실")
        self.assertEqual(cur.facts[50]["source_document_ids"], [1])

        # Conflict may insert for character 1 but related_fact_id must be None
        conflicts = [
            f
            for f in cur.facts.values()
            if f["status"] == pfs.STATUS_CONFLICT and f["character_id"] == 1
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertIsNone(conflicts[0]["related_fact_id"])

    def test_mark_document_processed_even_with_zero_facts(self):
        cur = FakeCursor()
        pfs.mark_document_processed(cur, character_id=1, document_id=55)
        markers = [
            f
            for f in cur.facts.values()
            if f["status"] == pfs.STATUS_DOCUMENT_PROCESSED
        ]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["field_name"], pfs.DOCUMENT_PROCESSED_FIELD)
        self.assertEqual(markers[0]["source_document_ids"], [55])

        pfs.mark_document_processed(cur, character_id=1, document_id=55)
        markers = [
            f
            for f in cur.facts.values()
            if f["status"] == pfs.STATUS_DOCUMENT_PROCESSED
        ]
        self.assertEqual(len(markers), 1)


class PromptContractTests(unittest.TestCase):
    def test_legacy_analyze_prompt_no_longer_prioritizes_first_source_only(self):
        captured = {}

        class FakeResponse:
            output_text = (
                "{"
                + ",".join(f'"{f}":"ok"' for f in PERSONA_FIELDS)
                + "}"
            )

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            class interactions:
                @staticmethod
                def create(model, input):
                    captured["prompt"] = input
                    return FakeResponse()

        with patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "test-model"},
        ), patch("app.services.character_analysis.genai.Client", FakeClient):
            analyze_character("원문", character_name="테스트캐릭터")

        prompt = captured["prompt"]
        self.assertIn("테스트캐릭터", prompt)
        self.assertIn(
            "모든 자료가 동일한 캐릭터에 관한 것이라면 순서와 관계없이 누적 분석에 활용하세요",
            prompt,
        )
        self.assertNotIn(
            "최초 원작 자료에 등장하는 인물을 분석 대상으로 삼으세요",
            prompt,
        )
        self.assertNotIn(
            "추가 자료가 최초 원작 자료의 인물과 동일 인물인지 불분명하다면",
            prompt,
        )

    def test_extract_operations_prompt_includes_character_name_and_accumulation(self):
        captured = {}

        class FakeResponse:
            output_text = '{"operations":[]}'

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            class interactions:
                @staticmethod
                def create(model, input):
                    captured["prompt"] = input
                    return FakeResponse()

        with patch.dict(
            "os.environ",
            {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": "test-model"},
        ), patch("app.services.character_analysis.genai.Client", FakeClient):
            ops = extract_document_fact_operations(
                character_name="홍길동",
                raw_text="홍길동은 의적이다.",
                existing_facts=[
                    {
                        "id": 1,
                        "field_name": "basic_info",
                        "fact_text": "조선 시대 인물",
                        "source_document_ids": [1],
                        "status": "active",
                        "related_fact_id": None,
                    }
                ],
                title="문서1",
                url="https://example.com/1",
            )

        self.assertEqual(ops, [])
        prompt = captured["prompt"]
        self.assertIn("홍길동", prompt)
        self.assertIn("누적된 기존 정보", prompt)
        self.assertIn("conflict", prompt)
        self.assertIn("duplicate", prompt)


class SequentialFlowTests(unittest.TestCase):
    def test_skip_processed_documents_and_synthesize_only_when_all_done(self):
        documents = [
            {
                "id": 1,
                "display_order": 1,
                "url": "https://a",
                "title": "A",
                "raw_text": "doc1",
            },
            {
                "id": 2,
                "display_order": 2,
                "url": "https://b",
                "title": "B",
                "raw_text": "doc2",
            },
            {
                "id": 3,
                "display_order": 3,
                "url": "https://c",
                "title": "C",
                "raw_text": "doc3",
            },
        ]
        persona_payload = {field: f"{field}-final" for field in PERSONA_FIELDS}
        calls = []

        def fake_process(character_id, character_name, document):
            calls.append(document["id"])

        with patch.object(pfs, "character_analysis_lock", _noop_lock), patch.object(
            pfs, "is_persona_confirmed", return_value=False
        ), patch.object(
            pfs, "get_character_name", return_value="캐릭터"
        ), patch.object(
            pfs, "list_source_documents", return_value=documents
        ), patch.object(
            pfs, "get_processed_document_ids", return_value={1}
        ), patch.object(
            pfs, "adopt_identical_reextracted_document", return_value=False
        ), patch.object(
            pfs, "process_document_facts", side_effect=fake_process
        ), patch.object(
            pfs,
            "list_content_facts",
            return_value=[
                {
                    "id": 1,
                    "field_name": "basic_info",
                    "fact_text": "x",
                    "source_document_ids": [1, 2, 3],
                    "status": "active",
                    "related_fact_id": None,
                }
            ],
        ), patch(
            "app.services.persona_facts_service.synthesize_persona_from_facts",
            return_value=persona_payload,
        ) as synth, patch.object(
            pfs, "save_unconfirmed_persona", return_value=99
        ):
            result = pfs.run_sequential_persona_analysis(1, user_id=7)

        self.assertEqual(calls, [2, 3])
        synth.assert_called_once()
        self.assertEqual(result["persona_id"], 99)
        self.assertEqual(result["documents_skipped"], 1)
        self.assertEqual(result["documents_processed"], 2)
        self.assertFalse(result["is_confirmed"])

    def test_skip_identical_reextracted_document_without_gemini(self):
        documents = [
            {
                "id": 10,
                "display_order": 1,
                "url": "https://a",
                "title": "A",
                "raw_text": "same text",
            },
            {
                "id": 11,
                "display_order": 1,
                "url": "https://a",
                "title": "A",
                "raw_text": "same text",
            },
        ]
        persona_payload = {field: f"{field}-final" for field in PERSONA_FIELDS}
        process_calls = []

        def fake_process(character_id, character_name, document):
            process_calls.append(document["id"])

        def fake_adopt(character_id, document_id):
            # New re-extracted doc 11 shares content with already-processed 10
            return document_id == 11

        with patch.object(pfs, "character_analysis_lock", _noop_lock), patch.object(
            pfs, "is_persona_confirmed", return_value=False
        ), patch.object(
            pfs, "get_character_name", return_value="캐릭터"
        ), patch.object(
            pfs, "list_source_documents", return_value=documents
        ), patch.object(
            pfs, "get_processed_document_ids", return_value={10}
        ), patch.object(
            pfs, "adopt_identical_reextracted_document", side_effect=fake_adopt
        ), patch.object(
            pfs, "process_document_facts", side_effect=fake_process
        ), patch.object(
            pfs,
            "list_content_facts",
            return_value=[
                {
                    "id": 1,
                    "field_name": "basic_info",
                    "fact_text": "x",
                    "source_document_ids": [10],
                    "status": "active",
                    "related_fact_id": None,
                }
            ],
        ), patch(
            "app.services.persona_facts_service.synthesize_persona_from_facts",
            return_value=persona_payload,
        ), patch.object(
            pfs, "save_unconfirmed_persona", return_value=5
        ):
            result = pfs.run_sequential_persona_analysis(1, user_id=7)

        self.assertEqual(process_calls, [])
        self.assertEqual(result["documents_skipped"], 2)
        self.assertEqual(result["documents_processed"], 0)

    def test_confirmed_persona_is_protected(self):
        with patch.object(pfs, "character_analysis_lock", _noop_lock), patch.object(
            pfs, "is_persona_confirmed", return_value=True
        ):
            with self.assertRaises(PermissionError):
                pfs.run_sequential_persona_analysis(1, user_id=7)

    def test_no_synthesis_when_document_processing_fails(self):
        documents = [
            {
                "id": 1,
                "display_order": 1,
                "url": "https://a",
                "title": "A",
                "raw_text": "doc1",
            },
            {
                "id": 2,
                "display_order": 2,
                "url": "https://b",
                "title": "B",
                "raw_text": "doc2",
            },
        ]

        def fail_on_second(character_id, character_name, document):
            if document["id"] == 2:
                raise RuntimeError("gemini failed")

        with patch.object(pfs, "character_analysis_lock", _noop_lock), patch.object(
            pfs, "is_persona_confirmed", return_value=False
        ), patch.object(
            pfs, "get_character_name", return_value="캐릭터"
        ), patch.object(
            pfs, "list_source_documents", return_value=documents
        ), patch.object(
            pfs, "get_processed_document_ids", return_value=set()
        ), patch.object(
            pfs, "adopt_identical_reextracted_document", return_value=False
        ), patch.object(
            pfs, "process_document_facts", side_effect=fail_on_second
        ), patch(
            "app.services.persona_facts_service.synthesize_persona_from_facts"
        ) as synth, patch.object(
            pfs, "save_unconfirmed_persona"
        ) as save:
            with self.assertRaises(RuntimeError):
                pfs.run_sequential_persona_analysis(1, user_id=7)

        synth.assert_not_called()
        save.assert_not_called()

    def test_concurrent_analyze_raises_in_progress(self):
        @contextmanager
        def busy_lock(_character_id: int):
            raise pfs.AnalysisInProgressError("analysis_in_progress")
            yield  # pragma: no cover

        with patch.object(pfs, "character_analysis_lock", busy_lock):
            with self.assertRaises(pfs.AnalysisInProgressError):
                pfs.run_sequential_persona_analysis(1, user_id=7)


class AnalysisLockTests(unittest.TestCase):
    def test_lock_uses_pg_try_advisory_lock_and_unlocks(self):
        lock_conn = MagicMock()
        lock_cur = MagicMock()
        lock_conn.cursor.return_value.__enter__.return_value = lock_cur
        lock_cur.fetchone.return_value = (True,)

        with patch.object(pfs, "get_connection", return_value=lock_conn):
            with pfs.character_analysis_lock(42):
                pass

        executed_sql = [
            " ".join(call.args[0].split())
            for call in lock_cur.execute.call_args_list
        ]
        self.assertTrue(
            any("pg_try_advisory_lock" in sql for sql in executed_sql)
        )
        self.assertTrue(
            any("pg_advisory_unlock" in sql for sql in executed_sql)
        )
        lock_conn.close.assert_called_once()

    def test_lock_busy_raises(self):
        lock_conn = MagicMock()
        lock_cur = MagicMock()
        lock_conn.cursor.return_value.__enter__.return_value = lock_cur
        lock_cur.fetchone.return_value = (False,)

        with patch.object(pfs, "get_connection", return_value=lock_conn):
            with self.assertRaises(pfs.AnalysisInProgressError):
                with pfs.character_analysis_lock(42):
                    pass


class EmptyExtractionSuccessTests(unittest.TestCase):
    def test_empty_operations_still_mark_processed(self):
        document = {
            "id": 9,
            "display_order": 1,
            "url": "https://x",
            "title": "X",
            "raw_text": "관련 정보 없음",
        }
        fake_conn = MagicMock()
        fake_cur = FakeCursor()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur

        with patch.object(pfs, "list_content_facts", return_value=[]), patch(
            "app.services.persona_facts_service.extract_document_fact_operations",
            return_value=[],
        ), patch.object(pfs, "get_connection", return_value=fake_conn):
            pfs.process_document_facts(1, "캐릭터", document)

        markers = [
            f
            for f in fake_cur.facts.values()
            if f["status"] == pfs.STATUS_DOCUMENT_PROCESSED
        ]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["source_document_ids"], [9])


class AdoptIdenticalDocumentTests(unittest.TestCase):
    def test_adopt_marks_new_document_when_twin_found(self):
        fake_conn = MagicMock()
        fake_cur = FakeCursor()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = fake_cur

        with patch.object(
            pfs, "find_processed_identical_document", return_value=10
        ), patch.object(pfs, "get_connection", return_value=fake_conn):
            adopted = pfs.adopt_identical_reextracted_document(1, 11)

        self.assertTrue(adopted)
        markers = [
            f
            for f in fake_cur.facts.values()
            if f["status"] == pfs.STATUS_DOCUMENT_PROCESSED
        ]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["source_document_ids"], [11])

    def test_adopt_returns_false_when_no_twin(self):
        with patch.object(
            pfs, "find_processed_identical_document", return_value=None
        ):
            self.assertFalse(pfs.adopt_identical_reextracted_document(1, 11))


if __name__ == "__main__":
    unittest.main()
