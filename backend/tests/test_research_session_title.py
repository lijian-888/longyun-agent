import unittest

from app.research_sessions import (
    DEFAULT_RESEARCH_SESSION_TITLE,
    summarize_research_session_title,
)


class ResearchSessionTitleTests(unittest.TestCase):
    def test_removes_polite_and_task_prefixes(self) -> None:
        self.assertEqual(
            summarize_research_session_title("请帮我分析一下耐盐碱稻材料的性状表现？"),
            "耐盐碱稻材料的性状表现",
        )

    def test_uses_first_semantic_sentence(self) -> None:
        self.assertEqual(
            summarize_research_session_title("如何筛选高产矮秆材料？请同时说明证据来源。"),
            "如何筛选高产矮秆材料",
        )

    def test_normalizes_markdown_and_whitespace(self) -> None:
        self.assertEqual(
            summarize_research_session_title("##   比较  两个亲本的遗传距离\n并给出建议"),
            "两个亲本的遗传距离",
        )

    def test_caps_long_titles(self) -> None:
        title = summarize_research_session_title("请分析" + "耐盐碱水稻多年多点表型与环境互作" * 4)
        self.assertLessEqual(len(title), 32)
        self.assertTrue(title.endswith("…"))

    def test_empty_question_falls_back_to_default(self) -> None:
        self.assertEqual(summarize_research_session_title("  "), DEFAULT_RESEARCH_SESSION_TITLE)


if __name__ == "__main__":
    unittest.main()
