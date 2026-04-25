#!/usr/bin/env python3
"""
scan_knowledge_base.py: 扫描知识库所有文档，建立索引缓存
用法: python scan_knowledge_base.py [--output cache.json] [--system lowlatency|dingding|all]
"""

import argparse
import json
import subprocess
import os
import time

# 知识库配置
SPACE_IDS = {
    "低延时": "lowlatency_space_id",  # 需要替换为实际值
    "顶点": "dingding_space_id",
    "星河": "7628219860123667634"
}

# 系统关键词映射（用于判断问题属于哪个系统）
SYSTEM_TRIGGERS = {
    "低延时": ["低延时", "lowlatency", "latency"],
    "顶点": ["顶点", "dingding", "现货", "期货", "symbol"],
}

DEFAULT_SPACE_ID = "7628219860123667634"  # 星河（主知识库）


def run_command(cmd):
    """执行 shell 命令并返回输出"""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return result.stdout, result.stderr, result.returncode


def detect_system(query):
    """根据问题关键词判断属于哪个系统"""
    query_lower = query.lower()
    for system, triggers in SYSTEM_TRIGGERS.items():
        for t in triggers:
            if t.lower() in query_lower:
                return system
    return "all"


def search_docs(query, space_id=None, page_size=50):
    """搜索文档"""
    if space_id:
        cmd = f'lark-cli docs +search --query "{query}" --page-size {page_size} --format json'
    else:
        cmd = f'lark-cli docs +search --query "{query}" --page-size {page_size} --format json'

    stdout, stderr, code = run_command(cmd)
    if code != 0:
        return []

    try:
        data = json.loads(stdout)
        if data.get("ok") and data.get("data", {}).get("results"):
            return data["data"]["results"]
    except:
        pass
    return []


def fetch_doc_content(token, doc_type):
    """获取文档内容摘要"""
    if doc_type == "WIKI":
        import platform
        if platform.system() == 'Windows':
            get_node_cmd = f'lark-cli wiki spaces get_node --params "{{\\"token\\":\\"{token}\\"}}"'
        else:
            get_node_cmd = f"lark-cli wiki spaces get_node --params '{{\\\"token\\\":\\\"{token}\\\"}}'"
        stdout, stderr, code = run_command(get_node_cmd)
        if code == 0:
            try:
                node_data = json.loads(stdout)
                if node_data.get("ok"):
                    token = node_data["data"]["node"]["obj_token"]
                    doc_type = node_data["data"]["node"]["obj_type"].upper()
            except:
                pass

    if doc_type in ["DOCX", "DOC"]:
        cmd = f'lark-cli docs +fetch --doc {token} --format json'
        stdout, stderr, code = run_command(cmd)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("ok"):
                    content = data.get("data", {}).get("markdown", "")
                    # 提取前500字符作为摘要
                    return content[:500] if content else ""
            except:
                pass
    return ""


def get_all_docs_from_space(space_id, system_name):
    """获取某个知识库空间下的所有文档"""
    docs = []

    # 搜索该系统下的所有文档
    results = search_docs(f"system:{system_name}", space_id, page_size=100)

    # 也用空查询看能不能获取目录结构
    if not results:
        results = search_docs("", space_id, page_size=100)

    for item in results:
        meta = item.get("result_meta", {})
        doc_type = meta.get("doc_types", "")

        # 获取内容摘要
        token = meta.get("token", "")
        content_snippet = fetch_doc_content(token, doc_type)

        docs.append({
            "title": meta.get("title", ""),
            "url": meta.get("url", ""),
            "token": token,
            "doc_type": doc_type,
            "system": system_name,
            "content_snippet": content_snippet[:300],  # 截取前300字符
            "indexed_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })

    return docs


def build_index():
    """扫描所有知识库，建立索引"""
    index = {
        "version": "1.0",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "spaces": {},
        "documents": [],
        "keyword_index": {}  # 关键词到文档的映射
    }

    # 扫描星河（主知识库）
    print("[扫描] 星河知识库...")
    docs = get_all_docs_from_space(DEFAULT_SPACE_ID, "星河")
    index["documents"].extend(docs)
    index["spaces"]["星河"] = {
        "space_id": DEFAULT_SPACE_ID,
        "doc_count": len(docs)
    }
    print(f"  找到 {len(docs)} 篇文档")

    # 更新关键词索引
    for doc in docs:
        # 用标题分词建立索引
        title = doc["title"]
        keywords = extract_keywords(title)
        for kw in keywords:
            if kw not in index["keyword_index"]:
                index["keyword_index"][kw] = []
            index["keyword_index"][kw].append(doc["title"])

    return index


def extract_keywords(text):
    """从文本中提取关键词（简单分词）"""
    import re
    # 提取中文词和英文词
    words = re.findall(r'[\w]+', text)
    return [w for w in words if len(w) >= 2]


def save_index(index, output_path):
    """保存索引到文件"""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"[保存] 索引已保存到 {output_path}")


def main():
    parser = argparse.ArgumentParser(description="扫描知识库建立索引")
    parser.add_argument("--output", "-o", default="kb_index.json", help="输出索引文件路径")
    parser.add_argument("--system", "-s", choices=["低延时", "顶点", "all"], default="all", help="扫描哪个系统")

    args = parser.parse_args()

    print(f"[开始] 扫描知识库...")
    print(f"[时间] {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    index = build_index()

    save_index(index, args.output)

    print()
    print(f"[完成] 共索引 {len(index['documents'])} 篇文档")
    print(f"[关键词] {len(index['keyword_index'])} 个")


if __name__ == "__main__":
    main()