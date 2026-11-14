import os
import unittest
from unittest.mock import patch
from urllib.parse import quote

from fastapi import HTTPException

from app.routers.character_sources import extract_character_sources
from app.services.tavily_service import (
    character_name_from_url,
    search_character_material,
)

URL_A = "https://namu.wiki/w/%EB%8B%88%EB%82%98%EB%B8%8C"
URL_B = "https://namu.wiki/w/%EC%8B%A0%EB%8B%88"


class Cursor:
    def __init__(self, duplicate_text=None, existing_count=0):
        self.duplicate_text = duplicate_text
        self.existing_count = existing_count
        self.inserts = []
        self._fetchone_value = None
        self._fetchall_value = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        if "FROM characters c" in compact:
            self._fetchall_value = [(1, URL_A), (2, URL_B)]
            return
        if "COUNT(*)" in compact:
            self._fetchone_value = (self.existing_count,)
            return
        if "INSERT INTO character_source_documents" in compact:
            self.inserts.append(params)
            self._fetchone_value = (100 + len(self.inserts),)
            return
        if "FROM character_source_documents" in compact:
            raw_text = params[1]
            self._fetchone_value = (7,) if raw_text == self.duplicate_text else None
            return
        if "FROM character_urls" in compact:
            url = params[1]
            self._fetchone_value = (1,) if url == URL_A else (2,)
            return
        raise AssertionError(compact)

    def fetchone(self):
        value = self._fetchone_value
        self._fetchone_value = None
        return value

    def fetchall(self):
        value = self._fetchall_value
        self._fetchall_value = None
        return value


class Conn:
    def __init__(self, cursor):
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_obj


class NameAndSearchTests(unittest.TestCase):
    def test_character_name_comes_from_namu_path(self):
        self.assertEqual(character_name_from_url(URL_A), "니나브")
        self.assertEqual(
            character_name_from_url("https://namu.wiki/w/니나브/작중 행적"),
            "니나브",
        )

    @patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"})
    @patch("app.services.tavily_service.TavilyClient")
    def test_search_keeps_only_relevant_obtained_body(self, client_cls):
        client_cls.return_value.search.return_value = {
            "results": [
                {
                    "title": "다른 인물 - 나무위키",
                    "url": "https://namu.wiki/w/다른인물",
                    "content": "니나브가 잠시 언급된다.",
                    "raw_content": "다른 문서의 본문",
                },
                {
                    "title": "니나브 - 나무위키",
                    "url": "https://namu.wiki/w/" + quote("니나브"),
                    "content": "짧은 설명",
                    "raw_content": "실제 확보된 니나브 본문",
                },
                {
                    "title": "니나브 - 나무위키",
                    "url": "https://example.com/nina",
                    "content": "나무위키가 아닌 본문",
                    "raw_content": "외부 사이트 본문",
                },
                {
                    "title": "니나브 - 나무위키",
                    "url": "https://namu.wiki/w/니나브",
                    "content": "   ",
                    "raw_content": None,
                },
            ]
        }

        hits = search_character_material(URL_A)

        self.assertEqual(
            hits,
            [
                {
                    "title": "니나브 - 나무위키",
                    "url": "https://namu.wiki/w/" + quote("니나브"),
                    "raw_content": "실제 확보된 니나브 본문",
                }
            ],
        )
        self.assertEqual(
            client_cls.return_value.search.call_args.kwargs["query"],
            "니나브 나무위키",
        )

    @patch.dict(os.environ, {"TAVILY_API_KEY": "test-key"})
    @patch("app.services.tavily_service.TavilyClient")
    def test_snippet_is_saved_when_full_body_is_absent(self, client_cls):
        client_cls.return_value.search.return_value = {
            "results": [
                {
                    "title": "니나브 - 나무위키",
                    "url": "https://namu.wiki/w/니나브",
                    "content": "검색으로 받은 문단",
                    "raw_content": None,
                }
            ]
        }

        hits = search_character_material(URL_A)

        self.assertEqual(hits[0]["raw_content"], "검색으로 받은 문단")


class ExtractFallbackTests(unittest.TestCase):
    def _run(self, extract_value, search_hits, extract_error=False, **cursor_kwargs):
        cursor = Cursor(**cursor_kwargs)
        searched = []

        def search(url):
            searched.append(url)
            return search_hits.get(url, [])

        with (
            patch(
                "app.routers.character_sources.extract_urls",
                side_effect=RuntimeError("extract down") if extract_error else None,
                return_value=None if extract_error else extract_value,
            ),
            patch(
                "app.routers.character_sources.search_character_material",
                side_effect=search,
            ),
            patch(
                "app.routers.character_sources.get_connection",
                return_value=Conn(cursor),
            ),
            patch(
                "app.routers.character_sources.save_document_chunks",
            ) as chunks,
        ):
            result = extract_character_sources(3, 9)

        return result, cursor, searched, chunks

    def test_successful_extract_does_not_search(self):
        result, cursor, searched, chunks = self._run(
            {
                "results": [
                    {"url": URL_A, "title": "니나브", "raw_content": "추출 본문 A"},
                    {"url": URL_B, "title": "신닌", "raw_content": "추출 본문 B"},
                ],
                "failed_results": [],
            },
            {},
        )

        self.assertEqual(result["saved_count"], 2)
        self.assertEqual(searched, [])
        self.assertEqual(len(cursor.inserts), 2)
        chunks.assert_not_called()

    def test_failed_url_uses_search_and_keeps_extract(self):
        result, cursor, searched, chunks = self._run(
            {
                "results": [
                    {"url": URL_A, "title": "니나브", "raw_content": "추출 본문"}
                ],
                "failed_results": [{"url": URL_B, "error": "fetch failed"}],
            },
            {
                URL_B: [
                    {
                        "title": "신닌 - 나무위키",
                        "url": "https://namu.wiki/w/신닌",
                        "raw_content": "검색으로 확보한 본문",
                    }
                ]
            },
        )

        self.assertEqual(searched, [URL_B])
        self.assertEqual(result["saved_count"], 2)
        self.assertEqual(result["message"], "원작 자료를 추출하고 저장했습니다.")
        self.assertEqual(cursor.inserts[0][2], "추출 본문")
        self.assertEqual(cursor.inserts[1][2], "검색으로 확보한 본문")
        self.assertEqual(cursor.inserts[1][0], 2)
        chunks.assert_called_once_with(102)

    def test_same_body_is_not_stored_twice(self):
        result, cursor, searched, chunks = self._run(
            {
                "results": [
                    {"url": URL_A, "title": "니나브", "raw_content": "같은 본문"}
                ],
                "failed_results": [{"url": URL_B}],
            },
            {
                URL_B: [
                    {
                        "title": "신닌 - 나무위키",
                        "url": URL_B,
                        "raw_content": "같은   본문",
                    }
                ]
            },
        )

        self.assertEqual(searched, [URL_B])
        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(cursor.inserts[0][2], "같은 본문")
        chunks.assert_not_called()

    def test_extract_outage_can_be_replaced_by_search(self):
        result, cursor, searched, chunks = self._run(
            None,
            {
                URL_A: [
                    {
                        "title": "니나브 - 나무위키",
                        "url": URL_A,
                        "raw_content": "대체 본문",
                    }
                ]
            },
            extract_error=True,
        )

        self.assertEqual(searched, [URL_A, URL_B])
        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(cursor.inserts[0][2], "대체 본문")
        chunks.assert_called_once_with(101)

    def test_both_sources_fail(self):
        with self.assertRaises(HTTPException) as raised:
            self._run(
                {"results": [], "failed_results": [{"url": URL_A}, {"url": URL_B}]},
                {},
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("반환하지 않았습니다", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
