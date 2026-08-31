import unittest

from app.main import knowledge_lexical_matches


class KnowledgeHybridRetrievalTests(unittest.TestCase):
    def test_exact_material_identifier_survives_strict_vector_threshold(self):
        matches = knowledge_lexical_matches(
            "HNNF-G001 的育种证据是什么？",
            "资料涉及材料 HNNF-G001，并记录了田间试验来源。",
        )
        self.assertEqual(matches, ["hnnf-g001"])

    def test_two_controlled_topics_enable_short_document_fallback(self):
        matches = knowledge_lexical_matches(
            "请归集亲本和性状证据",
            "本文记录亲本组合及其性状观测。",
        )
        self.assertEqual(set(matches), {"亲本", "性状"})

    def test_one_broad_topic_does_not_enable_fallback(self):
        self.assertEqual(knowledge_lexical_matches("查询材料", "材料说明"), [])


if __name__ == "__main__":
    unittest.main()
