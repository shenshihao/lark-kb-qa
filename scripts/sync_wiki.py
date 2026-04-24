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
        encoding='utf-8',
        errors='replace'
    )
    return result.stdout, result.stderr, result.returncode


def get_wiki_nodes(space_id: str) -> list:
    """获取知识库根节点下的所有节点"""
    params = {"space_id": space_id}
    params_str = json.dumps(params).replace('"', '\\"')
    cmd = f"lark-cli wiki nodes list --params \"{params_str}\""
    stdout, stderr, code = run_command(cmd)

    if code != 0:
        print(f"获取知识库节点失败: {stderr}")
        return []

    try:
        if stdout is None:
            return []
        data = json.loads(stdout)
        if data.get("code") == 0:
            return data.get("data", {}).get("items", [])
    except (json.JSONDecodeError, TypeError):
        pass

    return []


def get_node_children(parent_node_token: str, space_id: str = None) -> list:
    """获取节点的子节点"""
    params = {"parent_node_token": parent_node_token}
    if space_id:
        params["space_id"] = space_id
    params_str = json.dumps(params).replace('"', '\\"')
    cmd = f"lark-cli wiki nodes list --params \"{params_str}\""
    stdout, stderr, code = run_command(cmd)

    if code != 0:
        return []

    try:
        if stdout is None:
            return []
        data = json.loads(stdout)
        if data.get("code") == 0:
            return data.get("data", {}).get("items", [])
    except (json.JSONDecodeError, TypeError):
        pass

    return []


def get_doc_info(doc_token: str) -> dict:
    """获取飞书原生文档信息"""
    cmd = f'lark-cli docs +fetch --doc {doc_token} --format json'
    stdout, stderr, code = run_command(cmd)

    if code != 0:
        return {}

    try:
        data = json.loads(stdout)
        if data.get("code") == 0:
            return {
                "doc_id": doc_token,
                "doc_title": data.get("data", {}).get("title", ""),
                "doc_type": "native",
                "doc_url": f"https://Feishu.cn/docx/{doc_token}"
            }
    except json.JSONDecodeError:
        pass

    return {}


def get_file_info(node: dict) -> dict:
    """获取文件节点信息

    Args:
        node: wiki 节点 dict，包含 obj_token 和 title

    Returns:
        dict 包含 doc_id, doc_title, doc_type, doc_url, file_token, file_ext
    """
    obj_token = node.get("obj_token", "")
    node_token = node.get("node_token", "")
    title = node.get("title", "")

    if not obj_token:
        return {}

    # 从文件名提取扩展名
    if "." in title:
        file_ext = title.rsplit(".", 1)[-1].lower()
    else:
        file_ext = ""

    # 支持的文件类型
    supported_types = {
        "xlsx": "excel",
        "xls": "excel",
        "docx": "word",
        "doc": "word",
        "pdf": "pdf",
        "pptx": "pptx",
        "ppt": "pptx",
        "csv": "csv"
    }

    doc_type = supported_types.get(file_ext, "")

    return {
        "doc_id": node_token,  # 使用 node_token 作为文档ID
        "doc_title": title,
        "doc_type": doc_type,
        "doc_url": f"https://Feishu.cn/docx/{node_token}",
        "file_token": obj_token,
        "file_ext": file_ext
    }


def download_file(file_token: str, output_filename: str) -> tuple:
    """下载文件

    Returns: (success, error_msg)
    """
    # 确保使用相对路径（lark-cli 要求）
    if not output_filename.startswith('./'):
        output_filename = './' + output_filename
    cmd = f'lark-cli drive +download --file-token "{file_token}" --output "{output_filename}" --overwrite'
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        encoding='utf-8',
        errors='replace'
    )
    return result.returncode == 0, result.stderr if result.returncode != 0 else ""


def cleanup_file(filename: str):
    """清理下载的文件"""
    try:
        if filename and os.path.exists(filename):
            os.remove(filename)
    except:
        pass


import os


def process_node(node: dict, depth: int = 0) -> int:
    """递归处理节点，返回处理的文档数"""
    doc_count = 0
    node_token = node.get("node_token", "")
    node_name = node.get("title", "")
    node_type = node.get("obj_type", "")

    indent = "  " * depth
    print(f"{indent}处理节点: {node_name} ({node_type})")

    # 飞书原生文档
    if node_type == "docx" or node.get("parent_token"):
        doc_info = get_doc_info(node_token)
        if doc_info:
            success = index_document(doc_info)
            if success:
                doc_count += 1
                print(f"{indent}  [OK] indexed: {doc_info.get('doc_title', '')}")
            else:
                print(f"{indent}  [FAIL] index failed: {doc_info.get('doc_title', '')}")

    # 文件附件类型 (xlsx, xls, docx, pdf, pptx, csv等)
    elif node_type == "file":
        file_info = get_file_info(node)
        if file_info and file_info.get("doc_type"):
            success = index_file_document(file_info)
            if success:
                doc_count += 1
                print(f"{indent}  [OK] indexed: {file_info.get('doc_title', '')}")
            else:
                print(f"{indent}  [FAIL] index failed: {file_info.get('doc_title', '')}")
        else:
            print(f"{indent}  [SKIP] unsupported file type: {node_name}")

    # 如果有子节点，递归处理
    children = get_node_children(node_token, KNOWLEDGE_SPACE_ID)
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

    # 解析 JSON 提取 markdown 内容
    try:
        json_data = json.loads(content)
        if json_data.get("ok"):
            content = json_data.get("data", {}).get("markdown", "")
    except (json.JSONDecodeError, TypeError):
        pass

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


def index_file_document(file_info: dict) -> bool:
    """索引文件附件 (Excel, Word, PDF, PPT等)

    1. 下载文件
    2. 解析内容
    3. 文本分块
    4. 添加到 BM25 索引
    """
    doc_id = file_info.get("doc_id")
    doc_title = file_info.get("doc_title", "")
    doc_url = file_info.get("doc_url", "")
    file_token = file_info.get("file_token", "")
    file_ext = file_info.get("file_ext", "")

    if not file_token or not file_ext:
        bm25_index.log_sync(doc_id, "incremental", "failed", "missing file token or extension")
        return False

    # 构建下载文件名（使用相对路径，lark-cli 要求）
    output_filename = f"{file_token}.{file_ext}"
    if not output_filename.startswith('./'):
        output_filename = './' + output_filename

    # 下载文件（lark-cli 要求相对路径）
    cmd = f'lark-cli drive +download --file-token "{file_token}" --output "{output_filename}" --overwrite'
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        encoding='utf-8',
        errors='replace'
    )

    if result.returncode != 0:
        bm25_index.log_sync(doc_id, "incremental", "failed", f"download failed: {result.stderr}")
        return False

    # 解析 JSON 获取保存路径
    saved_path = None
    try:
        data = json.loads(result.stdout)
        if data.get("ok"):
            saved_path = data.get("data", {}).get("saved_path")
    except (json.JSONDecodeError, TypeError):
        pass

    if not saved_path or not os.path.exists(saved_path):
        bm25_index.log_sync(doc_id, "incremental", "failed", f"file not found at: {saved_path}")
        return False

    try:
        # 解析文件内容
        if file_ext in ("xlsx", "xls"):
            content, parse_err = doc_parser.parse_excel(saved_path)
        elif file_ext in ("docx", "doc"):
            content, parse_err = doc_parser.parse_word(saved_path)
        elif file_ext == "pdf":
            content, parse_err = doc_parser.parse_pdf(saved_path)
        elif file_ext in ("pptx", "ppt"):
            content, parse_err = doc_parser.parse_pptx(saved_path)
        elif file_ext == "csv":
            content, parse_err = doc_parser.parse_csv(saved_path)
        else:
            bm25_index.log_sync(doc_id, "incremental", "failed", f"unsupported type: {file_ext}")
            return False

        if parse_err or not content:
            bm25_index.log_sync(doc_id, "incremental", "failed", f"parse failed: {parse_err}")
            return False

    finally:
        # 清理下载的文件
        cleanup_file(saved_path)

    # 分块
    chunks = text_chunker.chunk_document(doc_id, doc_title, content, file_ext)

    if not chunks:
        bm25_index.log_sync(doc_id, "incremental", "skipped", "no content")
        return False

    # 添加文档到索引表
    bm25_index.add_doc(
        doc_id=doc_id,
        doc_title=doc_title,
        doc_type=file_ext,
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