#!/usr/bin/env python3
"""
search_hybrid.py: 混合检索（关键词 + 向量）
用法: python search_hybrid.py "用户问题" [--top-k 5] [--mode both|keyword|vector]
"""

import argparse
import json
import sys
import os
from pathlib import Path

# 添加父目录到路径，以便导入 embedding_cache
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.embedding_cache import get_embedding, cosine_similarity, load_vector_cache

# lark-cli 搜索
def html_escape(text):
    """安全转义 HTML 特殊字符"""
    if not text:
        return ""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;"))


def keyword_search(query, max_results=20):
    """调用 lark-cli 进行关键词搜索"""
    cmd = ["lark-cli", "docs", "+search", "--query", query, "--page-size", str(max_results), "--format", "json"]
    result = __import__('subprocess').run(
        cmd,
        capture_output=True,
        cwd=Path(__file__).parent.parent
    )
    stdout = result.stdout.decode('utf-8', errors='replace') if result.stdout else ""

    if result.returncode != 0 or not stdout:
        return []

    try:
        data = json.loads(stdout)
        results = []
        if data.get("ok") and data.get("data", {}).get("results"):
            for item in data["data"]["results"][:max_results]:
                meta = item.get("result_meta", {})
                results.append({
                    "title": meta.get("title", "") or "无标题",
                    "url": meta.get("url", ""),
                    "token": meta.get("token", ""),
                    "doc_type": meta.get("doc_types", ""),
                    "summary": item.get("summary_highlighted", ""),
                    "score": 1.0,  # 关键词搜索没有相关性分数
                    "source": "keyword"
                })
        return results
    except json.JSONDecodeError:
        return []


def vector_search(query, top_k=5):
    """向量检索"""
    cache = load_vector_cache()

    if not cache["documents"]:
        return []

    try:
        query_embedding = get_embedding(query)
    except Exception as e:
        print(f"[警告] 向量检索失败: {e}")
        return []

    similarities = []
    for i, doc in enumerate(cache["documents"]):
        vec = cache["vectors"][i]
        sim = cosine_similarity(query_embedding, vec)
        similarities.append((sim, doc))

    similarities.sort(reverse=True)

    results = []
    for sim, doc in similarities[:top_k]:
        results.append({
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "token": doc.get("token", ""),
            "doc_type": doc.get("doc_type", ""),
            "summary": doc.get("content", "")[:200] if doc.get("content") else "",
            "score": sim,
            "source": "vector"
        })
    return results


def hybrid_search(query, top_k=5, mode="both"):
    """混合检索

    mode:
      both    - 关键词 + 向量，结果合并去重
      keyword - 仅关键词
      vector  - 仅向量（需要先建立索引）
    """
    keyword_results = []
    vector_results = []

    if mode in ("both", "keyword"):
        keyword_results = keyword_search(query, max_results=top_k * 2)

    if mode in ("both", "vector"):
        vector_results = vector_search(query, top_k=top_k)

    if mode == "both":
        # 合并去重
        seen = {}
        for r in keyword_results:
            if r["title"] not in seen:
                seen[r["title"]] = r

        for r in vector_results:
            if r["title"] not in seen:
                seen[r["title"]] = r

        merged = list(seen.values())
        # 按 score 排序
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[:top_k]

    elif mode == "keyword":
        return keyword_results[:top_k]

    else:  # vector
        return vector_results


def main():
    parser = argparse.ArgumentParser(description="混合检索（关键词 + 向量）")
    parser.add_argument("query", nargs="?", help="搜索查询")
    parser.add_argument("--top-k", type=int, default=5, help="返回前 k 条结果")
    parser.add_argument("--mode", choices=["both", "keyword", "vector"], default="both",
                        help="检索模式: both=混合, keyword=仅关键词, vector=仅向量")

    args = parser.parse_args()

    if not args.query:
        parser.print_help()
        return

    print(f"[检索] 模式: {args.mode}")
    print(f"[检索] 查询: {args.query}\n")

    results = hybrid_search(args.query, args.top_k, args.mode)

    print(f"[结果] 找到 {len(results)} 条:\n")
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r['title']}")
        print(f"      来源: {r['source']} | 相似度: {r['score']:.4f}")
        print(f"      链接: {r['url']}\n")


if __name__ == "__main__":
    main()
