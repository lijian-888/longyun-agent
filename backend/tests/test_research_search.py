import json
import os
import unittest
from unittest.mock import patch

import httpx

from app import research_search as search


VARIETY = "访问这个网站：https://www.ricedata.cn/ 找到：赣晚籼35号"
PAPER = "毒品成瘾相关神经系统和骨骼系统共损害的骨-脑轴机制研究进展"


class QueryTests(unittest.TestCase):
    def test_exact_variety_and_domain(self):
        self.assertTrue(search.needs_current_public_search(VARIETY))
        self.assertEqual(search.build_safe_public_query(VARIETY), ("赣晚籼35号", ["ricedata.cn"]))

    def test_exact_paper(self):
        question = "使用Tavily，访问知网：https://www.cnki.net/ 帮我搜索：" + PAPER
        self.assertEqual(search.build_safe_public_query(question), (PAPER, ["cnki.net"]))

    def test_local_only(self):
        for question in ["不要联网，搜索水稻文献", "仅从本地知识库检索水稻", "查询知识库中的水稻论文"]:
            self.assertFalse(search.needs_current_public_search(question), question)

    def test_implicit_query_is_generic(self):
        self.assertEqual(search.build_safe_public_query("我们的内部水稻材料编号ABC，近期论文怎么样")[0], "水稻 论文 公开研究资料")

    def test_public_url_boundary(self):
        self.assertEqual(search._public_url("https://www.ricedata.cn/"), "https://www.ricedata.cn/")
        for url in ["https://localhost/", "http://127.0.0.1/a", "http://172.16.123.193/", "http://[::1]/", "https://u:password@example.com/", "https://example.com/?token=abc", "https://example.com/?X-Amz-Signature=abc", "https://example.com:8443/", "http://node.internal/"]:
            self.assertIsNone(search._public_url(url), url)
        self.assertFalse(search._trusted("https://evilnature.com/", [])[0])
        self.assertFalse(search._trusted("https://cnki.net.attacker.com/", ["cnki.net"])[0])
        self.assertTrue(search._trusted("https://kns.cnki.net/article", ["cnki.net"])[0])

    def test_irrelevant_homepage_and_wrong_variety(self):
        self.assertFalse(search._relevant(PAPER, "中国知网", "中国学术期刊网络出版总库"))
        self.assertFalse(search._relevant("赣晚籼35号", "赣晚籼36号", "水稻新品种"))
        self.assertTrue(search._relevant("赣晚籼35号", "水稻品种", "赣晚籼35号的审定信息"))

    def test_context_and_fallback(self):
        result = search.PublicSearchResult("品种介绍", "https://www.ricedata.cn/variety/35", "公开的品种记录", "user_specified_site", "search+extract")
        context = search.build_public_web_context([result], None)
        self.assertEqual(json.loads(context)["results"][0]["retrieval_method"], "search+extract")
        fallback = search.build_public_search_fallback(context)
        self.assertIn(result.url, fallback)
        self.assertIn("不代表已完成综合分析", fallback)
        self.assertIsNone(search.build_public_search_fallback(""))
        self.assertEqual(search.build_public_web_context([], None), "")


class TavilyTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, question, handler, key="test-key"):
        requests = []

        def transport(request):
            requests.append((request.url.path, json.loads(request.content)))
            return handler(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        with patch.dict(os.environ, {"TAVILY_API_KEY": key}), patch.object(search.httpx, "AsyncClient", return_value=client):
            result = await search.search_public_references(question)
        return result, requests

    async def test_search_and_extract_exact_variety(self):
        url = "https://www.ricedata.cn/variety/35"

        def handler(request):
            if request.url.path == "/search":
                return httpx.Response(200, json={"results": [{"title": "赣晚籼35号", "url": url, "content": "赣晚籼35号品种资料"}]})
            return httpx.Response(200, json={"results": [{"url": url, "raw_content": "赣晚籼35号，公开品种正文"}], "failed_results": []})

        (results, note), calls = await self.execute(VARIETY, handler)
        self.assertEqual(len(results), 1)
        self.assertIsNone(note)
        self.assertEqual(results[0].retrieval_method, "search+extract")
        self.assertEqual([call[0] for call in calls], ["/search", "/extract"])
        self.assertEqual(calls[0][1]["query"], "赣晚籼35号")
        self.assertEqual(calls[0][1]["include_domains"], ["ricedata.cn"])
        self.assertEqual(calls[0][1]["search_depth"], "advanced")

    async def test_tavily_key_phrase_not_a_secret(self):
        (results, note), calls = await self.execute(
            "使用Tavily的密钥，访问知网：https://www.cnki.net/ 帮我搜索：" + PAPER,
            lambda _: httpx.Response(200, json={"results": []}),
        )
        self.assertEqual(calls[0][1]["query"], PAPER)
        self.assertIn("未找到", note)

    async def test_homepage_not_passed_as_paper(self):
        (results, note), calls = await self.execute("搜索：" + PAPER + " https://www.cnki.net/", lambda _: httpx.Response(200, json={"results": [{"title": "中国知网", "url": "https://www.cnki.net/", "content": "中国期刊论文平台"}]}))
        self.assertEqual(results, [])
        self.assertEqual(len(calls), 1)
        self.assertIn("不能据此断言", note)

    async def test_direct_detail_extract_when_search_empty(self):
        url = "https://www.ricedata.cn/variety/35"
        def handler(request):
            return httpx.Response(200, json={"results": []} if request.url.path == "/search" else {"results": [{"url": url, "title": "赣晚籼35号", "raw_content": "赣晚籼35号资料正文"}]})
        (results, _), calls = await self.execute("访问 " + url + " 找到：赣晚籼35号", handler)
        self.assertEqual(results[0].retrieval_method, "extract")
        self.assertEqual(len(calls), 2)

    async def test_extract_failure_keeps_snippets(self):
        def handler(request):
            if request.url.path == "/extract":
                return httpx.Response(500)
            return httpx.Response(200, json={"results": [{"title": "赣晚籼35号", "url": "https://www.ricedata.cn/variety/35", "content": "赣晚籼35号简介"}]})
        (results, note), _ = await self.execute(VARIETY, handler)
        self.assertEqual(len(results), 1)
        self.assertIn("不代表已读取全文", note)

    async def test_private_request_never_sent(self):
        for question in ["搜索附件中的水稻数据", "Tavily搜索 私有ABC试验", "搜索水稻 tvly-secret", "访问 http://172.16.123.193/ 找到水稻", "搜索水稻 密码abcd"]:
            (results, note), calls = await self.execute(question, lambda _: self.fail("private request sent"))
            self.assertEqual(calls, [])
            self.assertEqual(results, [])

    async def test_missing_key_and_http_errors(self):
        (_, note), calls = await self.execute(VARIETY, lambda _: self.fail("missing key"), key="")
        self.assertIn("未配置", note)
        self.assertEqual(calls, [])
        for status in [401, 429, 500]:
            (_, note), calls = await self.execute(VARIETY, lambda _, status=status: httpx.Response(status))
            self.assertIn("Tavily", note)
            self.assertEqual(len(calls), 1)

    async def test_bad_payload(self):
        (results, note), _ = await self.execute(VARIETY, lambda _: httpx.Response(200, json={"results": None}))
        self.assertEqual(results, [])
        self.assertIn("失败", note)


if __name__ == "__main__":
    unittest.main()
