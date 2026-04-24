#!/usr/bin/env python3
"""
sync_wiki.py: 飞书知识库同步脚本
遍历 Wiki 节点，获取文档内容并建立 BM25 索引
"""

import json
import subprocess
import sys
import time
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import bm25_index
from scripts import doc_parser
from scripts import text_chunker

# 知识库配置
KNOWLEDGE_SPACE_ID = "7628219860123667634"


def run_command(cmd):
    """执行 shell 命令并返回输出"""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True
    )
    return result.stdout, result.stderr, result.returncode


def get_wiki_nodes(space_id: str) -> list:
    """获取知识库根节点下的所有节点"""
    cmd = f'lark-cli wiki spaces get_node --params \'{{"token":"{space_id}"}}\''
    stdout, stderr, code = run_command(cmd)

    if code != 0:
        print(f"获取知识库节点失败: {stderr}")
        return []

    try:
        data = json.loads(stdout)
        if data.get("ok"):
            return data.get("data", {}).get("nodes", [])
    except json.JSONDecodeError:
        pass

    return []


def get_node_children(node_token: str) -> list:
    """获取节点的子节点"""
    cmd = f'lark-cli wiki spaces get_node --params \'{{"token":"{node_token}"}}\''
    stdout, stderr, code = run_command(cmd)

    if code != 0:
        return []

    try:
        data = json.loads(stdout)
        if data.get("ok"):
            return data.get("data", {}).get("nodes", [])
    except json.JSONDecodeError:
        pass

    return []


def get_doc_info(doc_token: str) -> dict:
    """获取文档信息"""
    cmd = f'lark-cli docs +fetch --doc {doc_token} --format json'
    stdout, stderr, code = run_command(cmd)

    if code != 0:
        return {}

    try:
        data = json.loads(stdout)
        if data.get("ok"):
            return {
                "doc_id": doc_token,
                "doc_title": data.get("data", {}).get("title", ""),
                "doc_type": "native",
                "doc_url": f"https://Feishu.cn/docx/{doc_token}"
            }
    except json.JSONDecodeError:
        pass

    return {}


def process_node(node: dict, depth: int = 0) -> int:
    """递归处理节点，返回处理的文档数"""
    doc_count = 0
    node_token = node.get("node_token", "")
    node_name = node.get("title", "")
    node_type = node.get("obj_type", "")

    indent = "  " * depth
    print(f"{indent}处理节点: {node_name} ({node_type})")

    # 如果是文档，提取内容并索引
    if node_type == "docx" or node.get("parent_token"):
        doc_info = get_doc_info(node_token)
        if doc_info:
            success = index_document(doc_info)
            if success:
                doc_count += 1
                print(f"{indent}  ✓ 已索引: {doc_info.get('doc_title', '')}")
            else:
                print(f"{indent}  ✗ 索引失败: {doc_info.get('doc_title', '')}")

    # 如果有子节点，递归处理
    children = get_node_children(node_token)
    for child in children:
        doc_count += process_node(child, depth + 1)

    return doc_count


def index_document(doc_info: dict) -> bool:
    """索引单个文档

    1. 获取文档内容
    2. 文本分块
    3. 添加到 BM25 索引
    """
    doc_id = doc_info.get("doc_id")
    doc_title = doc_info.get("doc_title", "")
    doc_url = doc_info.get("doc_url", "")

    # 获取文档内容
    cmd = f'lark-cli docs +fetch --doc {doc_id}'
    stdout, stderr, code = run_command(cmd)

    if code != 0 or not stdout:
        bm25_index.log_sync(doc_id, "incremental", "failed", stderr)
        return False

    content = stdout.strip()

    # 分块
    chunks = text_chunker.chunk_document(doc_id, doc_title, content, "native")

    if not chunks:
        bm25_index.log_sync(doc_id, "incremental", "skipped", "无内容")
        return False

    # 添加文档到索引表
    bm25_index.add_doc(
        doc_id=doc_id,
        doc_title=doc_title,
        doc_type="native",
        doc_url=doc_url
    )

    # 删除旧块（如果是更新）
    bm25_index.delete_doc_chunks(doc_id)

    # 添加新块
    bm25_index.add_chunks(chunks)

    bm25_index.log_sync(doc_id, "incremental", "success")
    return True


def full_sync():
    """全量同步"""
    print("=== 开始全量同步 ===\n")
    print(f"知识库 Space ID: {KNOWLEDGE_SPACE_ID}\n")

    # 初始化数据库
    bm25_index.init_db()

    # 获取根节点
    nodes = get_wiki_nodes(KNOWLEDGE_SPACE_ID)
    print(f"找到 {len(nodes)} 个根节点\n")

    total_docs = 0
    for node in nodes:
        total_docs += process_node(node)

    print(f"\n=== 全量同步完成 ===")
    print(f"共处理 {total_docs} 个文档")

    # 显示统计
    stats = bm25_index.get_stats()
    print(f"\n索引统计:")
    print(f"  文档数: {stats['doc_count']}")
    print(f"  块数: {stats['chunk_count']}")
    print(f"  数据库大小: {stats['db_size_mb']:.2f} MB")


def incremental_sync(doc_id: str = None):
    """增量同步"""
    if doc_id:
        print(f"=== 增量同步文档: {doc_id} ===\n")
        doc_info = get_doc_info(doc_id)
        if doc_info:
            success = index_document(doc_info)
            if success:
                print("增量同步成功")
            else:
                print("增量同步失败")
    else:
        print("=== 增量同步（全量重建）===\n")
        full_sync()


def add_synonyms_from_file(filepath: str):
    """从文件加载同义词"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= 2:
                    term = parts[0].strip()
                    synonyms = parts[1].strip()
                    if term and synonyms:
                        bm25_index.add_synonym(term, synonyms)
        print(f"已加载同义词: {filepath}")
    except Exception as e:
        print(f"加载同义词失败: {e}")


def main():
    """主入口"""
    import argparse
    parser = argparse.ArgumentParser(description="飞书知识库同步脚本")
    parser.add_argument("--full", action="store_true", help="全量同步")
    parser.add_argument("--doc", type=str, help="增量同步指定文档")
    parser.add_argument("--synonyms", type=str, help="从文件加载同义词")
    parser.add_argument("--stats", action="store_true", help="显示索引统计")
    parser.add_argument("--clear", action="store_true", help="清空索引")

    args = parser.parse_args()

    # 初始化数据库
    bm25_index.init_db()

    if args.stats:
        stats = bm25_index.get_stats()
        print("=== 索引统计 ===")
        print(f"文档数: {stats['doc_count']}")
        print(f"块数: {stats['chunk_count']}")
        print(f"同义词数: {stats['synonym_count']}")
        print(f"数据库大小: {stats['db_size_mb']:.2f} MB")
        return

    if args.clear:
        confirm = input("确认清空所有索引? (yes/no): ")
        if confirm == "yes":
            bm25_index.clear_all()
            print("索引已清空")
        return

    if args.synonyms:
        add_synonyms_from_file(args.synonyms)
        return

    if args.full:
        full_sync()
    elif args.doc:
        incremental_sync(args.doc)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()