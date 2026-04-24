#!/usr/bin/env python3
"""
bm25_index.py: 知识库 BM25 全文索引模块
基于 SQLite FTS5 实现本地 BM25 检索
"""

import sqlite3
import json
import os
import time
from pathlib import Path

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kb_bm25.db")


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库 Schema"""
    conn = get_db()
    cursor = conn.cursor()

    # 文档索引表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kb_docs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL UNIQUE,
            doc_title TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            doc_url TEXT NOT NULL,
            wiki_node TEXT,
            created_at INTEGER,
            updated_at INTEGER,
            indexed_at INTEGER
        )
    """)

    # 文档块索引表 (FTS5)
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks USING fts5(
            chunk_id,
            doc_id,
            chunk_index,
            title,
            content,
            is_title_chunk,
            tokenize='porter unicode61'
        )
    """)

    # 同义词词典表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kb_synonyms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL UNIQUE,
            synonyms TEXT NOT NULL
        )
    """)

    # 同步状态记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kb_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            sync_type TEXT NOT NULL,
            synced_at INTEGER NOT NULL,
            status TEXT NOT NULL,
            error_msg TEXT
        )
    """)

    # 配置表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kb_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_doc_id ON kb_docs(doc_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_doc_id ON kb_sync_log(doc_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_synonyms_term ON kb_synonyms(term)")

    conn.commit()
    return conn


def add_doc(doc_id, doc_title, doc_type, doc_url, wiki_node="", created_at=None, updated_at=None):
    """添加文档到索引表"""
    conn = get_db()
    cursor = conn.cursor()
    now = int(time.time())

    cursor.execute("""
        INSERT OR REPLACE INTO kb_docs (doc_id, doc_title, doc_type, doc_url, wiki_node, created_at, updated_at, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (doc_id, doc_title, doc_type, doc_url, wiki_node, created_at, updated_at, now))

    conn.commit()
    return cursor.lastrowid


def add_chunk(chunk_id, doc_id, chunk_index, title, content, is_title_chunk=0):
    """添加文档块到 FTS5 索引"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO kb_chunks (chunk_id, doc_id, chunk_index, title, content, is_title_chunk)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (chunk_id, doc_id, chunk_index, title, content, is_title_chunk))

    conn.commit()
    return cursor.lastrowid


def add_chunks(chunks):
    """批量添加文档块"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executemany("""
        INSERT INTO kb_chunks (chunk_id, doc_id, chunk_index, title, content, is_title_chunk)
        VALUES (?, ?, ?, ?, ?, ?)
    """, chunks)

    conn.commit()
    return len(chunks)


def delete_doc_chunks(doc_id):
    """删除文档的所有块"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM kb_chunks WHERE doc_id = ?", (doc_id,))
    conn.commit()
    return cursor.rowcount


def search_bm25(query, top_k=10, doc_id=None):
    """BM25 检索

    Args:
        query: 搜索查询
        top_k: 返回前 k 条结果
        doc_id: 可选，限定在指定文档内搜索

    Returns:
        [(chunk_id, doc_id, chunk_index, title, content, bm25_score), ...]
    """
    conn = get_db()
    cursor = conn.cursor()

    # 构建查询条件
    where_clause = "kb_chunks MATCH ?"
    params = [query]

    if doc_id:
        where_clause += " AND doc_id = ?"
        params.append(doc_id)

    # BM25 检索，带标题权重
    sql = f"""
        SELECT
            c.chunk_id,
            c.doc_id,
            c.chunk_index,
            c.title,
            c.content,
            c.is_title_chunk,
            bm25(kb_chunks) as score,
            d.doc_title,
            d.doc_url
        FROM kb_chunks c
        JOIN kb_docs d ON c.doc_id = d.doc_id
        WHERE {where_clause}
        ORDER BY score DESC
        LIMIT ?
    """
    params.append(top_k)

    cursor.execute(sql, params)
    rows = cursor.fetchall()

    # 应用标题权重：标题块分数 × 1.5
    results = []
    for row in rows:
        score = row["score"]
        if row["is_title_chunk"]:
            score *= 1.5
        results.append((
            row["chunk_id"],
            row["doc_id"],
            row["chunk_index"],
            row["title"],
            row["content"],
            score,
            row["doc_title"],
            row["doc_url"]
        ))

    # 重新按加权分数排序
    results.sort(key=lambda x: x[5], reverse=True)
    return results[:top_k]


def search_with_synonyms(query, top_k=10, doc_id=None):
    """带同义词展开的 BM25 检索

    1. 查询同义词词典展开搜索词
    2. 多路 BM25 并行检索
    3. 合并去重
    """
    conn = get_db()
    cursor = conn.cursor()

    # 展开同义词
    expanded_queries = [query]
    cursor.execute("SELECT synonyms FROM kb_synonyms WHERE term = ?", (query,))
    row = cursor.fetchone()
    if row and row["synonyms"]:
        synonyms = row["synonyms"].split(",")
        expanded_queries.extend([s.strip() for s in synonyms if s.strip()])

    # 多路召回
    all_results = {}
    for q in expanded_queries:
        try:
            results = search_bm25(q, top_k=top_k * 2, doc_id=doc_id)
            for r in results:
                chunk_key = (r[0], r[1], r[2])  # chunk_id, doc_id, chunk_index
                if chunk_key not in all_results or r[5] > all_results[chunk_key][5]:
                    all_results[chunk_key] = r
        except Exception as e:
            # 单个查询失败不影响其他查询
            pass

    # 按分数排序返回 top_k
    sorted_results = sorted(all_results.values(), key=lambda x: x[5], reverse=True)
    return sorted_results[:top_k]


def add_synonym(term, synonyms):
    """添加同义词"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO kb_synonyms (term, synonyms) VALUES (?, ?)
    """, (term, synonyms))
    conn.commit()


def get_synonyms(term):
    """获取同义词"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT synonyms FROM kb_synonyms WHERE term = ?", (term,))
    row = cursor.fetchone()
    return row["synonyms"].split(",") if row and row["synonyms"] else []


def log_sync(doc_id, sync_type, status, error_msg=""):
    """记录同步日志"""
    conn = get_db()
    cursor = conn.cursor()
    now = int(time.time())
    cursor.execute("""
        INSERT INTO kb_sync_log (doc_id, sync_type, synced_at, status, error_msg)
        VALUES (?, ?, ?, ?, ?)
    """, (doc_id, sync_type, now, status, error_msg))
    conn.commit()


def get_doc(doc_id):
    """获取文档信息"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM kb_docs WHERE doc_id = ?", (doc_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def get_all_docs():
    """获取所有文档"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM kb_docs ORDER BY indexed_at DESC")
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_stats():
    """获取索引统计"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as count FROM kb_docs")
    doc_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM kb_chunks")
    chunk_count = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM kb_synonyms")
    synonym_count = cursor.fetchone()["count"]

    # 获取数据库文件大小
    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0

    return {
        "doc_count": doc_count,
        "chunk_count": chunk_count,
        "synonym_count": synonym_count,
        "db_size_bytes": db_size,
        "db_size_mb": db_size / (1024 * 1024)
    }


def clear_all():
    """清空所有索引数据（谨慎使用）"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM kb_chunks")
    cursor.execute("DELETE FROM kb_docs")
    cursor.execute("DELETE FROM kb_sync_log")
    cursor.execute("DELETE FROM kb_synonyms")
    conn.commit()


def main():
    """测试/初始化数据库"""
    print("=== BM25 索引系统 ===")
    print()

    # 初始化数据库
    init_db()
    print("数据库初始化完成:", DB_PATH)
    print()

    # 显示统计
    stats = get_stats()
    print("索引统计:")
    print(f"  文档数: {stats['doc_count']}")
    print(f"  块数: {stats['chunk_count']}")
    print(f"  同义词数: {stats['synonym_count']}")
    print(f"  数据库大小: {stats['db_size_mb']:.2f} MB")


if __name__ == "__main__":
    main()