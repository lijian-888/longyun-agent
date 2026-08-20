import unittest

from app.conversation_title import auto_title_for_first_message, summarize_conversation_title


class ConversationTitleTests(unittest.TestCase):
    def test_summarizes_chinese_request_and_removes_courtesy_prefix(self):
        title = summarize_conversation_title(
            "请帮我分析隆两优华占在不同试验点的多年产量稳定性，并说明主要风险。"
        )

        self.assertTrue(title.startswith("分析隆两优华占"))
        self.assertLessEqual(len(title), 24)
        self.assertNotIn("请帮我", title)

    def test_cleans_markdown_and_uses_first_meaningful_sentence(self):
        title = summarize_conversation_title(
            "## 需求\n请比较三个候选材料的抗倒伏表现！后续再分析米质。"
        )

        self.assertEqual(title, "比较三个候选材料的抗倒伏表现")

    def test_truncates_long_english_title_at_a_word_boundary(self):
        title = summarize_conversation_title(
            "Please compare yield stability across all environments and explain the strongest interaction effects."
        )

        self.assertLessEqual(len(title), 24)
        self.assertTrue(title.endswith("…"))
        self.assertNotIn("Please", title)

    def test_only_titles_first_message_of_an_untouched_conversation(self):
        generated = auto_title_for_first_message("新会话", "分析今年的区域试验产量", has_messages=False)
        existing_message = auto_title_for_first_message("新会话", "第二轮问题", has_messages=True)
        manually_named = auto_title_for_first_message("我的重点课题", "首次问题", has_messages=False)

        self.assertEqual(generated, "分析今年的区域试验产量")
        self.assertIsNone(existing_message)
        self.assertIsNone(manually_named)


if __name__ == "__main__":
    unittest.main()
