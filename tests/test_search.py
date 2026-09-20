# -*- coding: utf-8 -*-
"""搜索模块自测: 离线解析 + 联网真实搜索"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import ws_search as ws  # noqa: E402

PASS, FAIL = [], []


def check(cond, title, extra=""):
    (PASS if cond else FAIL).append(title)
    print("  %s %s%s" % ("✓" if cond else "✗", title, ("  → " + str(extra)) if extra else ""))


print("--- 1) 离线: 两层转义的内嵌 JSON 能被抠出来 ---")
inner = json.dumps({
    "data": {"eresult": 1, "current_page": 1, "total_pages": 3, "total_count": 42,
             "results": [{"publishedfileid": "111", "title": "Test &amp;Mod",
                          "preview_url": "https://x/y.jpg", "short_description": "d",
                          "votes_up": 7, "subscriptions": 3, "rating": 4.5,
                          "tags": [{"tag": "Lua"}]}]}},
    ensure_ascii=False)
# 模拟页面里的两层转义
level1 = inner.replace("\\", "\\\\").replace('"', '\\"')
html = '<html><script>push([1,"[' + level1 + ']"]);</script></html>'
got = ws.extract_payload(html)
check(bool(got), "从内嵌 payload 抠出 results")
if got:
    check(len(got["results"]) == 1, "条目数正确")
    n = ws.normalize(got["results"][0])
    check(n["id"] == "111", "id 正确", n["id"])
    check(n["votes_up"] == 7 and n["subs"] == 3, "赞/订阅数解析", "%s/%s" % (n["votes_up"], n["subs"]))
    check(n["tags"] == ["Lua"], "标签解析", n["tags"])
    check(n["url"].endswith("id=111"), "详情链接生成")

print("\n--- 2) 离线: 只有一层转义也要能解析 ---")
level0 = inner
html2 = '<html><script>push([1,"[' + level0.replace('"', '\\"') + ']"]);</script></html>'
got2 = ws.extract_payload(html2)
check(bool(got2), "一层转义同样能抠出来")

print("\n--- 3) 联网: 三种排序都能搜到结果 ---")
for sort in ("trend", "recent", "top"):
    try:
        r = ws.search("music", sort, 1)
        items = r.get("items") or []
        check(len(items) >= 10, "排序 %s 有结果" % sort,
              "共 %s 条, 本页 %d 条" % (r.get("total"), len(items)))
        if items:
            it = items[0]
            check(bool(it["title"]) and bool(it["id"]), "  %s: 标题/ID 完整" % sort, it["title"][:30])
    except Exception as exc:
        check(False, "排序 %s 搜索成功" % sort, str(exc)[:70])

print("\n--- 4) 联网: 中文关键词 / 空关键词(浏览全部) ---")
for q in ("以撒", ""):
    try:
        r = ws.search(q, "trend", 1)
        check(len(r.get("items") or []) >= 10, "关键词 %r 有结果" % q,
              "共 %s 条" % r.get("total"))
    except Exception as exc:
        check(False, "关键词 %r" % q, str(exc)[:70])

print("\n--- 5) 预览图链接可用于界面展示 ---")
try:
    r = ws.search("music", "trend", 1)
    withprev = [i for i in r["items"] if i["preview"].startswith("http")]
    check(len(withprev) >= 10, "绝大多数条目带预览图", "%d/%d" % (len(withprev), len(r["items"])))
except Exception as exc:
    check(False, "预览图检查", str(exc)[:60])

print("\n" + "=" * 58)
print("通过 %d 项" % len(PASS) + ("  失败 %d 项 ✗" % len(FAIL) if FAIL else "  全部通过 ✓"))
if FAIL:
    for f in FAIL:
        print("  ✗", f)
print("=" * 58)
sys.exit(1 if FAIL else 0)
