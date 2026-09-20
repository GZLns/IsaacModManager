# -*- coding: utf-8 -*-
"""
Steam 创意工坊搜索（无需 API key、无需登录）

数据来源:
    创意工坊浏览页是服务端渲染的，页面里**内嵌了官方
    IPublishedFileService.QueryFiles 的完整 JSON 结果**
    （形如 {"data":{"eresult":1,"total_count":N,"results":[...]}}），
    只是被转义了两层塞在 JS 字符串里。把它取出来解析即可。

    为什么不用官方接口: QueryFiles 需要 WebAPI key，而不带 key 直接请求返回
    403；而浏览页这条路完全免费、不需要登录，也不需要用户做任何配置。

用法:
    python tools/ws_search.py "关键词" [排序] [页码]
    >>> {"ok": true, "total": 1789, "items": [...]}
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

APPID = "250900"          # The Binding of Isaac: Rebirth
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 界面上的三种排序 -> 工坊的 browsesort 参数
SORTS = {
    "trend": "trend",          # 热门
    "recent": "mostrecent",    # 最新
    "top": "toprated",         # 评分最高
}
BROWSE = "https://steamcommunity.com/workshop/browse/"


# ---------------------------------------------------------------- 转义解码
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
        "/": "/", "b": "\b", "f": "\f", "'": "'"}


def _unescape_once(text):
    """按 JS/JSON 字符串规则解一层转义"""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            out.append(_ESC.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _brace_slice(text, start):
    """从 text[start] 的 '{' 开始做括号匹配, 返回完整 JSON 片段"""
    if start < 0 or text[start] != "{":
        return None
    depth, i, n = 0, start, len(text)
    in_str, esc = False, False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def _find_results(obj):
    """在任意嵌套结构里找 results 列表"""
    if isinstance(obj, dict):
        r = obj.get("results")
        if isinstance(r, list) and r and isinstance(r[0], dict):
            return obj
        for v in obj.values():
            got = _find_results(v)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_results(v)
            if got is not None:
                return got
    return None


def extract_payload(html):
    """把内嵌的搜索结果 JSON 抠出来（逐层反转义, 哪层能解析用哪层）"""
    text = html
    for _ in range(4):
        if '"results"' in text:
            idx = text.find('"results"')
            # 从 results 往前逐个 '{' 试着解析(通常最近的一两层就行)
            starts = [m.start() for m in re.finditer(r"\{", text[:idx])][-12:]
            for st in reversed(starts):
                frag = _brace_slice(text, st)
                if not frag:
                    continue
                try:
                    obj = json.loads(frag)
                except Exception:
                    continue
                found = _find_results(obj)
                if found is not None:
                    return found
        text = _unescape_once(text)
    return None


# ---------------------------------------------------------------- 抓取解析
def _fetch(url, timeout=30):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def normalize(item):
    """把工坊条目压成界面要用的字段

    字段名以实测为准: 星级是 star_rating, 点赞数是 total_votes
    (不是 rating / votes_up), 且 file_size 是字符串。
    """
    pid = str(item.get("publishedfileid") or "")
    return {
        "id": pid,
        "title": (item.get("title") or "").strip(),
        "preview": item.get("preview_url") or "",
        "desc": (item.get("short_description") or "").strip(),
        "size": _num(item.get("file_size")),
        "updated": _num(item.get("time_updated")),
        "created": _num(item.get("time_created")),
        "subs": _num(item.get("subscriptions")) or _num(item.get("favorited")),
        "favorited": _num(item.get("favorited")),
        "views": _num(item.get("views")),
        "comments": _num(item.get("num_comments_public")),
        "votes_up": _num(item.get("total_votes")) or _num(item.get("votes_up")),
        "stars": round(float(item.get("star_rating") or item.get("rating") or 0), 1),
        "tags": [(t.get("display_name") or t.get("tag")) for t in (item.get("tags") or [])
                 if (t.get("display_name") or t.get("tag"))],
        "url": "https://steamcommunity.com/sharedfiles/filedetails/?id=" + pid,
    }


def search(text="", sort="trend", page=1, days=None, appid=APPID):
    """搜创意工坊。返回 {"ok":True,"total":..,"page":..,"pages":..,"items":[...]}"""
    sort = SORTS.get(sort, sort if sort in SORTS.values() else "trend")
    page = max(1, int(page or 1))
    params = {
        "appid": appid,
        "browsesort": sort,
        "section": "readytouseitems",
        "actualsort": sort,
        "p": page,
        "num_per_page": 30,
    }
    if text:
        params["searchtext"] = text
    if days:
        params["days"] = days
    url = BROWSE + "?" + urllib.parse.urlencode(params)

    html = _fetch(url)
    data = extract_payload(html)
    if not data:
        raise RuntimeError("没能从工坊页面解析出结果（页面结构可能变了）")

    items = [normalize(x) for x in data.get("results") or [] if x.get("publishedfileid")]
    return {
        "ok": True,
        "query": text,
        "sort": sort,
        "page": _num(data.get("current_page"), page),
        "pages": _num(data.get("total_pages")),
        "total": _num(data.get("total_count")),
        "items": items,
    }


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    sort = sys.argv[2] if len(sys.argv) > 2 else "trend"
    page = sys.argv[3] if len(sys.argv) > 3 else 1
    try:
        out = search(text, sort, page)
    except Exception as exc:
        out = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
