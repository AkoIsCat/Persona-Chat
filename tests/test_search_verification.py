import json
import unittest
from unittest.mock import patch

from app.services.search_verification import (
    search_document_title,
    select_verified_search_hits,
)


class _Interactions:
    def __init__(self, output_text):
        self.output_text = output_text
        self.prompts = []
        self.calls = 0

    def create(self, model, input):
        self.calls += 1
        self.prompts.append(input)
        if isinstance(self.output_text, Exception):
            raise self.output_text
        return type("Response", (), {"output_text": self.output_text})()


class _Client:
    def __init__(self, interactions):
        self.interactions = interactions


REFERENCES = [
    {
        "url": "https://namu.wiki/w/니나브",
        "title": "니나브",
        "raw_content": "니나브는 마왕성 소속이고 신닌과 같은 세계에 있다.",
    }
]

CANDIDATES = [
    {
        "registered_url": "https://namu.wiki/w/신닌",
        "search_url": "https://namu.wiki/w/신닌",
        "title": "신닌 - 나무위키",
        "raw_content": "신닌은 마왕성에서 니나브와 협력한다.",
    },
    {
        "registered_url": "https://namu.wiki/w/신닌",
        "search_url": "https://namu.wiki/w/신닌(다른작품)",
        "title": "신닌(동명이인) - 나무위키",
        "raw_content": "이 신닌은 다른 시대의 실존 인물이다.",
    },
]


class SearchVerificationTests(unittest.TestCase):
    def _select(self, output_text, references=None, candidates=None):
        interactions = _Interactions(output_text)
        with patch(
            "app.services.search_verification._gemini_client",
            return_value=(_Client(interactions), "gemini-3.5-flash-lite"),
        ):
            kept = select_verified_search_hits(
                REFERENCES if references is None else references,
                CANDIDATES if candidates is None else candidates,
            )
        return kept, interactions

    def test_one_call_keeps_only_supported_hit(self):
        kept, interactions = self._select(
            json.dumps(
                {
                    "results": [
                        {
                            "id": 0,
                            "same_character": True,
                            "reason": "마왕성 소속과 니나브와의 관계가 기준 자료와 같다.",
                        },
                        {
                            "id": 1,
                            "same_character": False,
                            "reason": "다른 작품의 동명이인이다.",
                        },
                    ]
                }
            )
        )

        self.assertEqual(interactions.calls, 1)
        self.assertEqual(kept, [CANDIDATES[0]])
        prompt = interactions.prompts[0]
        self.assertIn("동일한 작품 또는 세계관", prompt)
        self.assertIn("동일한 소속", prompt)
        self.assertIn("모순", prompt)
        self.assertIn("동명이인", prompt)
        self.assertIn("이름이나 일부 설정이 비슷하다는 이유만으로", prompt)
        self.assertIn("마왕성 소속", prompt)
        self.assertIn("https://namu.wiki/w/신닌(다른작품)", prompt)

    def test_same_url_without_reference_is_not_accepted_by_default(self):
        same_url = {
            "registered_url": "https://namu.wiki/w/니나브",
            "search_url": "https://namu.wiki/w/니나브",
            "title": "니나브 - 나무위키",
            "raw_content": "이름 소개만 있다.",
        }
        kept, interactions = self._select(
            json.dumps(
                {
                    "results": [
                        {
                            "id": 0,
                            "same_character": False,
                            "reason": "URL이 같지만 작품과 관계를 특정할 근거가 없다.",
                        }
                    ]
                }
            ),
            references=[],
            candidates=[same_url],
        )

        self.assertEqual(kept, [])
        prompt = interactions.prompts[0]
        self.assertIn("같다는 사실만으로 통과시키지 마세요", prompt)
        self.assertIn("등록 URL 경로", prompt)
        self.assertIn("/w/니나브", prompt)

    def test_missing_reason_or_omitted_id_is_excluded(self):
        kept, _interactions = self._select(
            json.dumps(
                {
                    "results": [
                        {"id": 0, "same_character": True, "reason": "   "},
                    ]
                }
            )
        )

        self.assertEqual(kept, [])

    def test_invalid_response_excludes_every_candidate(self):
        kept, _interactions = self._select("검증할 수 없습니다.")

        self.assertEqual(kept, [])

    def test_search_url_is_stored_in_title(self):
        self.assertEqual(
            search_document_title("신닌 - 나무위키", "https://namu.wiki/w/신닌"),
            "신닌 - 나무위키 | 검색URL: https://namu.wiki/w/신닌",
        )


if __name__ == "__main__":
    unittest.main()
