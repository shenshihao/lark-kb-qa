#!/usr/bin/env python3
"""
embedding_cache.py: 文档向量化缓存
用法: python embedding_cache.py --build    # 从知识库构建向量索引
                            --query "文本"  # 查询单条文本的向量
                            --search "问题" # 搜索最相似的文档
"""

import argparse
import json
import os
import time
from pathlib import Path

# Jina AI 配置
JINA_API_URL = "https://api.jina.ai/embed"
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")

# 向量缓存文件
CACHE_DIR = Path(__file__).parent.parent
VECTOR_CACHE = CACHE_DIR / "vector_cache.json"

# 向量维度（Jina v2 base-zh 是 768维）
VECTOR_DIM = 768


def get_embedding(text, model="jina-embeddings-v2-base-zh"):
    """调用 Jina AI API 获取文本向量"""
    if not JINA_API_KEY:
        raise ValueError("未设置 JINA_API_KEY 环境变量")

    import requests

    response = requests.post(
        JINA_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {JINA_API_KEY}"
        },
        json={"model": model, "text": text},
        timeout=30
    )

    if response.status_code != 200:
        raise RuntimeError(f"Jina API 错误: {response.status_code} - {response.text}")

    result = response.json()
    return result["data"]["embedding"]


def cosine_similarity(a, b):
    """计算余弦相似度"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    return dot / (norm_a * norm_b + 1e-9)


def load_vector_cache():
    """加载向量缓存"""
    if VECTOR_CACHE.exists():
        return json.loads(VECTOR_CACHE.read_text(encoding="utf-8"))
    return {"version": "1.0", "documents": [], "vectors": []}


def save_vector_cache(cache):
    """保存向量缓存"""
    VECTOR_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def build_index(docs):
    """构建向量索引

    docs: [{"title": "", "url": "", "content": "", "token": ""}, ...]
    """
    cache = load_vector_cache()

    # 已有文档的去重
    existing = {(d["title"], d["url"]) for d in cache["documents"]}
    new_docs = [d for d in docs if (d["title"], d["url"]) not in existing]

    if not new_docs:
        print("[索引] 没有新文档需要向量化")
        return

    print(f"[索引] 准备向量化 {len(new_docs)} 篇文档...")

    vectors = []
    for i, doc in enumerate(new_docs):
        # 提取文档文本（前 500 字用于向量化）
        text = f"{doc['title']} {doc.get('content', '')[:500]}"

        try:
            embedding = get_embedding(text)
            vectors.append(embedding)
            doc["embedding_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            cache["documents"].append(doc)
            print(f"[{i+1}/{len(new_docs)}] 已向量化: {doc['title'][:30]}")
        except Exception as e:
            print(f"[{i+1}/{len(new_docs)}] 向量化失败: {doc['title'][:30]} - {e}")

        # 控制速率，避免 API 限流
        time.sleep(0.2)

    cache["vectors"].extend(vectors)
    save_vector_cache(cache)
    print(f"[索引] 完成，当前共 {len(cache['documents'])} 篇文档")


def search_vector(query, top_k=5):
    """向量检索：找到与 query 最相似的文档"""
    cache = load_vector_cache()

    if not cache["documents"]:
        return []

    # 查询向量化
    query_embedding = get_embedding(query)

    # 计算相似度
    similarities = []
    for i, doc in enumerate(cache["documents"]):
        vec = cache["vectors"][i]
        sim = cosine_similarity(query_embedding, vec)
        similarities.append((sim, doc))

    # 排序返回 top_k
    similarities.sort(reverse=True)
    return [{"score": sim, "doc": doc} for sim, doc in similarities[:top_k]]


def main():
    parser = argparse.ArgumentParser(description="文档向量化缓存工具")
    parser.add_argument("--build", action="store_true", help="从知识库构建向量索引")
    parser.add_argument("--query", type=str, help="查询单条文本的向量")
    parser.add_argument("--search", type=str, help="搜索最相似的文档")
    parser.add_argument("--top-k", type=int, default=5, help="返回前 k 条结果")
    parser.add_argument("--clear", action="store_true", help="清空向量缓存")

    args = parser.parse_args()

    if args.clear:
        if VECTOR_CACHE.exists():
            VECTOR_CACHE.unlink()
            print("[清空] 向量缓存已清空")
        else:
            print("[清空] 缓存文件不存在")
        return

    if args.query:
        print(f"[查询] 正在向量化: {args.query[:50]}...")
        embedding = get_embedding(args.query)
        print(f"[完成] 向量维度: {len(embedding)}")
        return

    if args.search:
        print(f"[搜索] 正在检索: {args.search[:50]}...")
        results = search_vector(args.search, args.top_k)
        print(f"[结果] 找到 {len(results)} 条相似文档:")
        for r in results:
            print(f"  相似度: {r['score']:.4f} | {r['doc']['title']}")
        return

    if args.build:
        print("[构建] 请使用 scan_knowledge_base.py 扫描文档后，再用此工具向量化")
        print("[提示] 目前需要手动将文档传递给 build_index()")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
