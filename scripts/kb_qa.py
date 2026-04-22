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

# MiniMax API 配置
MINIMAX_API_URL = "https://api.minimaxi.com/anthropic/v1/messages"
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")

# 知识库配置
KNOWLEDGE_SPACE_ID = "7628219860123667634"

# 系统关键词映射（用于判断问题属于哪个系统）
SYSTEM_KEYWORDS = {
    "低延时": ["低延时", "lowlatency", "low-latency", "latency", "wtfs", "委托方式", "client_feature"],
    "顶点": ["顶点", "dingding", "现货", "期货", "symbol", "wtfs"],
}

# 系统文档标题关键词（用于过滤搜索结果）
SYSTEM_TITLE_KEYWORDS = {
    "低延时": ["低延时", "lowlatency", "low-latency"],
    "顶点": ["顶点", "dingding", "hts"],
}


def run_command(cmd):
    """执行 shell 命令并返回输出"""
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    stdout = result.stdout.decode('utf-8', errors='replace') if result.stdout else ""
    stderr = result.stderr.decode('utf-8', errors='replace') if result.stderr else ""
    return stdout, stderr, result.returncode


def detect_system(question):
    """根据问题关键词判断属于哪个系统"""
    question_lower = question.lower()

    # 先检测是否是明确的系统问题
    has_lowlatency = any(t.lower() in question_lower for t in SYSTEM_KEYWORDS["低延时"])
    has_dingding = any(t.lower() in question_lower for t in SYSTEM_KEYWORDS["顶点"])

    # 如果两个都有，根据第一个出现的关键词判断
    if has_lowlatency and has_dingding:
        idx_low = min((question_lower.find(t.lower()) for t in SYSTEM_KEYWORDS["低延时"] if t.lower() in question_lower), default=999)
        idx_ding = min((question_lower.find(t.lower()) for t in SYSTEM_KEYWORDS["顶点"] if t.lower() in question_lower), default=999)
        if idx_low <= idx_ding:
            return "低延时"
        else:
            return "顶点"

    if has_lowlatency:
        return "低延时"
    if has_dingding:
        return "顶点"

    return "all"


def filter_results_by_system(results, system):
    """严格过滤结果，只保留指定系统的文档"""
    if system == "all":
        return results

    filtered = []
    system_kws = SYSTEM_TITLE_KEYWORDS.get(system, [])

    for r in results:
        title = r.get("title", "").lower()
        url = r.get("url", "").lower()

        # 检查标题或URL是否包含系统关键词
        matched = any(kw.lower() in title or kw.lower() in url for kw in system_kws)

        if matched:
            filtered.append(r)
        # 如果标题不包含任何系统关键词（如通用文档），也保留
        elif not any(kw.lower() in title for kw_list in SYSTEM_TITLE_KEYWORDS.values() for kw in kw_list):
            filtered.append(r)

    return filtered


def generate_search_queries(question, system):
    """调用 LLM 生成多个搜索角度的查询词"""
    if not MINIMAX_API_KEY:
        return expand_query_fallback(question)

    prompt = f"""你是一个知识库搜索专家。用户会问各种业务问题，你需要生成多个不同角度的搜索词来帮助找到相关文档。

**用户问题：**
{question}

**目标系统分类：**
{system}

**要求：**
1. 生成 3-6 个不同角度的搜索词
2. 每个搜索词简短，不超过20字符
3. 考虑同义词、相关概念
4. 如果问的是低延时系统，搜索词要围绕低延时展开
5. 如果问的是顶点系统，搜索词要围绕顶点展开
6. 如果是"低延时XXX"，搜索词应该是"低延时 XXX"或"低延时 委托方式 XXX"

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
                return [q.strip() for q in queries if q.strip()][:6]
    except:
        pass

    return expand_query_fallback(question)


def expand_query_fallback(question):
    """备用扩展逻辑"""
    queries = [question]

    # 对数字代码补充上下文
    numbers = re.findall(r'\d{4,}', question)
    for num in numbers:
        queries.extend([
            f"{num}",
            f"接口 {num}",
            f"委托方式 {num}",
        ])

    # 对wtfs补充
    if "wtfs" in question.lower():
        queries.extend([
            "wtfs 委托方式",
            "委托方式 wtfs",
            "wtfs",
        ])

    return list(set(queries))[:6]


def search_lark_cli(query, system="all", max_results=10):
    """调用 lark-cli 搜索知识库"""
    cmd = f'lark-cli docs +search --query "{query}" --page-size {max_results} --format json'
    stdout, stderr, code = run_command(cmd)

    if code != 0 or not stdout:
        return [], f"搜索失败: {stderr or '无输出'}"

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
                })
        return results, None
    except json.JSONDecodeError as e:
        return [], f"解析搜索结果失败: {e}"


def search_with_system_filter(question, system="all", max_results=5):
    """使用多查询词搜索，并对结果严格按系统过滤"""
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

    # 合并所有结果
    merged = list(all_results.values())

    # 严格按系统过滤
    if system != "all":
        filtered = filter_results_by_system(merged, system)
        print(f"[系统过滤] 原始 {len(merged)} 条 -> 过滤后 {len(filtered)} 条 ({system})")
        return filtered

    return merged


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

    # 搜索（带系统过滤）
    print("[搜索] 调用 lark-cli 搜索...")
    results = search_with_system_filter(args.question, detected, args.max_results)

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