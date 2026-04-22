#!/usr/bin/env python3
"""
lark-kb-qa: 飞书知识库问答
用法: python kb_qa.py --question "问题" [--system 低延时|顶点|all] [--max-results 5]
"""

import argparse
import json
import subprocess
import os
import re
import time

# MiniMax API 配置
MINIMAX_API_URL = "https://api.minimaxi.com/anthropic/v1/messages"
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")

# 知识库配置
KNOWLEDGE_SPACE_ID = "7628219860123667634"

# 系统关键词映射
SYSTEM_KEYWORDS = {
    "低延时": ["低延时", "lowlatency", "low-latency", "latency"],
    "顶点": ["顶点", "dingding", "现货", "期货", "symbol"],
}

# 系统路由配置
SYSTEM_ROUTING = {
    "低延时": "低延时",
    "顶点": "顶点",
    "all": "all"
}


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


def detect_system(question):
    """根据问题关键词判断属于哪个系统"""
    question_lower = question.lower()
    for system, triggers in SYSTEM_KEYWORDS.items():
        for t in triggers:
            if t.lower() in question_lower:
                return system
    return "all"


def load_index(index_path="kb_index.json"):
    """加载缓存索引"""
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return None


def search_index(index, query, system="all"):
    """在索引中搜索文档"""
    if not index:
        return []

    query_lower = query.lower()
    matched = []

    # 优先在关键词索引中找
    for keyword, titles in index.get("keyword_index", {}).items():
        if keyword in query_lower or query_lower in keyword:
            for title in titles:
                for doc in index.get("documents", []):
                    if doc["title"] == title:
                        if system == "all" or doc.get("system") == system:
                            matched.append(doc)

    # 如果没找到，用标题模糊匹配
    if not matched:
        for doc in index.get("documents", []):
            title = doc.get("title", "").lower()
            if query_lower in title or any(kw in title for kw in query_lower.split()):
                if system == "all" or doc.get("system") == system:
                    matched.append(doc)

    return matched[:10]  # 最多返回10条


def generate_search_queries(question, system):
    """调用 LLM 生成多个搜索角度的查询词"""
    if not MINIMAX_API_KEY:
        # 没有 API Key 时返回默认扩展
        return expand_query_fallback(question)

    prompt = f"""你是一个知识库搜索专家。用户会问各种业务问题，你需要生成多个不同角度的搜索词来帮助找到相关文档。

**用户问题：**
{question}

**系统分类：**
{system}

**要求：**
1. 生成 3-5 个不同角度的搜索词
2. 每个搜索词都应该能独立搜索
3. 考虑同义词、相关概念、常见表述
4. 如果问题涉及数字代码（如 7100701），要补充业务上下文（如"接口"、"symbol"、"合约"）
5. 搜索词要简洁，不超过 20 个字符

**输出格式（只输出搜索词，每行一个，不要其他内容）："""

    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 200,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MINIMAX_API_KEY}"
    }

    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(
            MINIMAX_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get("content"):
                queries = result["content"][0]["text"].strip().split('\n')
                # 清理并返回
                return [q.strip() for q in queries if q.strip()][:6]
    except:
        pass

    return expand_query_fallback(question)


def expand_query_fallback(question):
    """备用扩展逻辑（当 LLM 不可用时）"""
    queries = [question]

    # 对数字代码补充上下文
    import re
    numbers = re.findall(r'\d{4,}', question)
    for num in numbers:
        queries.extend([
            f"{num} 接口",
            f"{num} symbol",
            f"{num} 合约",
            f"{num} 说明"
        ])

    # 通用扩展
    if "什么" in question or "是" in question:
        queries.append(question.replace("是什么", "").replace("什么", ""))
    if "怎么" in question or "如何" in question:
        queries.append(question.replace("怎么", "").replace("如何", ""))

    return list(set(queries))[:6]


def search_lark_cli(query, system="all", max_results=5):
    """调用 lark-cli 搜索知识库"""
    if system != "all" and system in SYSTEM_KEYWORDS:
        # 添加系统标识
        query = f"{query} {system}"

    cmd = f'lark-cli docs +search --query "{query}" --page-size {max_results} --format json'
    stdout, stderr, code = run_command(cmd)

    if code != 0:
        return [], f"搜索失败: {stderr}"

    try:
        data = json.loads(stdout)
        results = []
        if data.get("ok") and data.get("data", {}).get("results"):
            for item in data["data"]["results"][:max_results]:
                meta = item.get("result_meta", {})
                results.append({
                    "title": meta.get("title", ""),
                    "url": meta.get("url", ""),
                    "token": meta.get("token", ""),
                    "doc_type": meta.get("doc_types", ""),
                    "summary": item.get("summary_highlighted", ""),
                })
        return results, None
    except json.JSONDecodeError as e:
        return [], f"解析搜索结果失败: {e}"


def search_with_multi_queries(question, system="all", max_results=5):
    """使用多个查询词搜索并合并结果"""
    # 1. 首先尝试 LLM 生成搜索词
    queries = generate_search_queries(question, system)

    print(f"[查询扩展] 尝试 {len(queries)} 个搜索词: {queries[:3]}...")

    all_results = {}
    for q in queries:
        results, error = search_lark_cli(q, system, max_results)
        if error:
            continue
        for r in results:
            if r["title"] not in all_results:
                all_results[r["title"]] = r

    return list(all_results.values())


def fetch_document_content(doc_token, doc_type):
    """获取文档内容"""
    if doc_type == "WIKI":
        get_node_cmd = f'lark-cli wiki spaces get_node --params \'{{"token":"{doc_token}"}}\''
        stdout, stderr, code = run_command(get_node_cmd)
        if code == 0:
            try:
                node_data = json.loads(stdout)
                if node_data.get("ok"):
                    doc_token = node_data["data"]["node"]["obj_token"]
                    doc_type = node_data["data"]["node"]["obj_type"].upper()
            except:
                pass

    if doc_type in ["DOCX", "DOC"]:
        cmd = f'lark-cli docs +fetch --doc {doc_token} --format json'
        stdout, stderr, code = run_command(cmd)
        if code == 0:
            try:
                data = json.loads(stdout)
                if data.get("ok"):
                    content = data.get("data", {}).get("markdown", "")[:4000]
                    return content, None
            except:
                pass

    return "", f"不支持的文档类型或获取失败: {doc_type}"


def generate_answer(question, context):
    """调用 MiniMax LLM 生成答案"""
    if not MINIMAX_API_KEY:
        return None, "未设置 MINIMAX_API_KEY 环境变量"

    prompt = f"""你是知识库问答助手。请根据以下检索到的知识库内容，回答用户的问题。

**用户问题：**
{question}

**检索到的知识库内容：**
{context}

**要求：**
1. 基于检索内容回答，不要编造答案
2. 如果检索内容不足以回答，请说明"根据现有知识库内容无法完全回答此问题"
3. 回答要简洁、准确
4. 如有需要，可以列举具体的数据或步骤
5. 在答案最后，列出参考的文档来源

**答案：**"""

    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 1000,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MINIMAX_API_KEY}"
    }

    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(
            MINIMAX_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get("content"):
                return result["content"][0]["text"], None
            return None, "LLM 返回格式错误"
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else str(e)
        return None, f"API 请求失败: {e.code} - {error_body}"
    except Exception as e:
        return None, f"API 请求异常: {str(e)}"


def main():
    parser = argparse.ArgumentParser(description="飞书知识库问答")
    parser.add_argument("--question", "-q", required=True, help="用户问题")
    parser.add_argument("--system", "-s", default="auto", help="系统: 低延时|顶点|all|auto (默认 auto)")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="最大检索文档数")
    parser.add_argument("--api-key", "-k", default=None, help="MiniMax API Key")
    parser.add_argument("--index", default="kb_index.json", help="本地索引文件路径")

    args = parser.parse_args()

    if args.api_key:
        os.environ["MINIMAX_API_KEY"] = args.api_key

    # 自动检测系统
    if args.system == "auto":
        detected = detect_system(args.question)
        print(f"[检测] 系统: {detected}")
    else:
        detected = args.system if args.system != "auto" else "all"

    print(f"[问题] {args.question}")
    print(f"[系统] {detected}")
    print()

    # 尝试加载本地索引（暂时禁用，因为索引构建还不完善）
    # index = load_index(args.index)
    index = None

    if index:
        print("[使用] 本地索引缓存")
        results = search_index(index, args.question, detected)
    else:
        print("[搜索] 调用 lark-cli 搜索...")
        results = search_with_multi_queries(args.question, detected, args.max_results)

    if not results:
        print("[未找到相关文档]")
        return

    print(f"[找到 {len(results)} 篇相关文档]\n")

    # 获取文档内容
    print("[获取文档内容]\n")
    contexts = []
    sources = []

    for i, doc in enumerate(results, 1):
        print(f"  [{i}] {doc['title']}")
        content, error = fetch_document_content(doc["token"], doc["doc_type"])
        if content:
            contexts.append(f"【{doc['title']}】\n{content[:1000]}...")
            sources.append(f"- {doc['title']}: {doc['url']}")
        else:
            if doc.get('summary'):
                contexts.append(f"【{doc['title']}】\n{doc['summary']}")
                sources.append(f"- {doc['title']}: {doc['url']}")

    if not contexts:
        print("[无法获取文档内容]")
        return

    # 生成答案
    print("\n[生成答案]\n")

    context_text = "\n\n".join(contexts)
    answer, error = generate_answer(args.question, context_text)

    if error:
        print(f"[错误] {error}")
        print("\n--- 参考文档 ---")
        print("\n".join(sources))
        return

    print("=" * 50)
    print("答案:")
    print("=" * 50)
    print(answer)
    print()
    print("=" * 50)
    print("参考文档:")
    print("=" * 50)
    print("\n".join(sources))


if __name__ == "__main__":
    main()