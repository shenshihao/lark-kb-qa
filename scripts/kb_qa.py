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

# 输入安全过滤规则（基于 RAG 提示工程最佳实践）
ATTACK_PATTERNS = [
    # 系统提示词泄露
    (r"忽略.*提示词", "检测到忽略提示词指令"),
    (r"你.*(设定|prompt|系统).*是", "检测到系统提示词查询"),
    (r"(prompt|系统).*泄露", "检测到提示词泄露尝试"),
    # 角色扮演/越狱
    (r"角色扮演", "检测到角色扮演请求"),
    (r"假设.*(黑客|攻击|违法)", "检测到恶意假设请求"),
    (r"你是.*(杀手|黑客|犯罪)", "检测到身份伪装请求"),
    # 目标劫持
    (r"忽略.*指令", "检测到指令劫持"),
    (r"忽略.*(上面|上述|之前)", "检测到历史指令忽略"),
    # 注入攻击
    (r"\\{wrap\\}", "检测到格式化注入"),
    (r"<[^>]+>", "检测到标签注入"),
]


def input_filter(question):
    """规则检查，拦截明显攻击性问题

    返回: (passed, reason) - passed=True 表示通过，reason=None
    """
    question_clean = question.strip()

    for pattern, reason in ATTACK_PATTERNS:
        if re.search(pattern, question_clean, re.IGNORECASE):
            return False, reason

    # 检查是否过短（可能是试探性输入）
    if len(question_clean) < 2:
        return False, "问题过短"

    return True, None


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


def expand_query_for_retry(question):
    """为空结果时的扩展查询（更通用的表达）"""
    queries = [question]

    # 提取核心业务词
    business_terms = []
    business_terms.extend([
        "规则", "流程", "条件", "说明",
        "配置", "接口", "参数", "说明",
        "多少", "如何", "怎么", "是什么"
    ])

    # 提取主要名词（去除停用词后的第一个词）
    stop_words = {"的", "了", "是", "在", "和", "与", "或", "及", "等", "我", "你", "他", "她", "它", "这", "那"}
    words = re.findall(r'[\w]+', question)
    main_terms = [w for w in words if w not in stop_words and len(w) >= 2]
    if main_terms:
        # 取前两个有意义的词
        core = "".join(main_terms[:2])
        queries.append(core)

    return list(set(queries))[:3]


def search_with_hybrid(question, system="all", max_results=5):
    """混合检索：关键词搜索 + 向量检索融合

    当向量索引存在时，将向量检索结果与关键词结果合并去重，
    并按 score 排序返回。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from scripts.embedding_cache import get_embedding, cosine_similarity, load_vector_cache

    # 1. 关键词搜索结果
    keyword_results = search_with_system_filter(question, system, max_results * 2)

    # 2. 向量搜索结果（如果有索引）
    vector_results = []
    cache = load_vector_cache()

    if cache["documents"]:
        try:
            query_embedding = get_embedding(question)
            similarities = []
            for i, doc in enumerate(cache["documents"]):
                vec = cache["vectors"][i]
                sim = cosine_similarity(query_embedding, vec)
                similarities.append((sim, doc))

            similarities.sort(reverse=True)
            for sim, doc in similarities[:max_results]:
                vector_results.append({
                    "title": doc.get("title", ""),
                    "url": doc.get("url", ""),
                    "token": doc.get("token", ""),
                    "doc_type": doc.get("doc_type", ""),
                    "summary": doc.get("content", "")[:200] if doc.get("content") else "",
                    "score": sim,
                    "source": "vector"
                })
        except Exception as e:
            print(f"[向量检索] 跳过: {e}")

    # 3. 合并去重
    seen = {}
    for r in keyword_results:
        r["source"] = "keyword"
        seen[r["title"]] = r

    for r in vector_results:
        if r["title"] not in seen:
            seen[r["title"]] = r

    merged = list(seen.values())
    merged.sort(key=lambda x: x.get("score", 1.0), reverse=True)
    return merged[:max_results]


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
                    "file_type": meta.get("file_type", ""),
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


def search_with_retry(question, system="all", max_results=5, max_retries=1):
    """带空结果自动重试的搜索

    如果首次搜索结果为空，自动扩展查询词重试。
    """
    # 首次搜索
    results = search_with_system_filter(question, system, max_results)

    # 如果有结果，直接返回
    if results:
        return results

    # 空结果，重试一次
    if max_retries > 0:
        print(f"[重试] 首次搜索无结果，尝试扩展查询...")

        # 使用更通用的扩展词重试
        retry_queries = expand_query_for_retry(question)
        print(f"[重试] 扩展查询词: {retry_queries}")

        for q in retry_queries:
            if q == question:
                continue  # 跳过原始问题
            results = search_with_system_filter(q, system, max_results)
            if results:
                print(f"[重试成功] 使用 '{q}' 找到 {len(results)} 条结果")
                return results

    return []


def fetch_document_content(doc_token, doc_type, file_type=""):
    """获取文档内容

    Args:
        doc_token: 文档 token
        doc_type: 文档类型 (DOCX, DOC, FILE, SHEET, WIKI, PDF, XLSX, XLS, CSV)
        file_type: 文件类型 (当 doc_type=FILE 时，用于区分 xlsx/docx/pdf)
    """
    if doc_type == "WIKI":
        get_node_cmd = f'lark-cli wiki spaces get_node --params \'{{"token":"{doc_token}"}}\''
        stdout, stderr, code = run_command(get_node_cmd)
        if code == 0:
            try:
                node_data = json.loads(stdout)
                if node_data.get("ok"):
                    doc_token = node_data["data"]["node"]["obj_token"]
                    doc_type = node_data["data"]["node"]["obj_type"].upper()
                    file_type = node_data["data"]["node"].get("file_type", "")
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

    # FILE 类型：需要根据 file_type 判断
    if doc_type == "FILE":
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from scripts.parse_utils import parse_document

        # file_type 为空时，默认尝试获取元数据判断
        if not file_type:
            from scripts.parse_utils import get_file_token_from_doc
            file_token, err = get_file_token_from_doc(doc_token)
            if err:
                return "", f"获取 file_token 失败: {err}"
            # file_type 未知时，先尝试 xlsx
            return parse_document(doc_token, "XLSX", max_chars=1000)

        # 根据 file_type 分发
        if file_type.lower() in ("xlsx", "xls"):
            return parse_document(doc_token, "XLSX", max_chars=1000)
        elif file_type.lower() == "pdf":
            return parse_document(doc_token, "PDF", max_chars=1000)
        elif file_type.lower() == "csv":
            return parse_document(doc_token, "CSV", max_chars=1000)
        elif file_type.lower() == "docx":
            # docx 走 docs +fetch
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
            return "", f"docx 文件获取失败"
        else:
            return "", f"不支持的 FILE 类型: {file_type}"

    # SHEET 类型：飞书多维表格
    if doc_type == "SHEET":
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from scripts.parse_utils import parse_document
        return parse_document(doc_token, "XLSX", max_chars=1000)

    # PDF/Excel/CSV 格式，使用 parse_utils
    if doc_type in ("PDF", "XLSX", "XLS", "CSV"):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from scripts.parse_utils import parse_document
        return parse_document(doc_token, doc_type, max_chars=1000)

    return "", f"不支持的文档类型或获取失败: {doc_type}"


def generate_answer(question, context):
    """调用 MiniMax LLM 生成答案（增强版 Prompt）"""
    if not MINIMAX_API_KEY:
        return None, "未设置 MINIMAX_API_KEY 环境变量"

    prompt = f"""你是知识库问答助手。请根据以下检索到的知识库内容，回答用户的问题。

<user_question>
{question}
</user_question>

<knowledge_context>
{context}
</knowledge_context>

**回答要求：**
1. 基于检索内容回答，不要编造答案
2. 如果检索内容不足以回答，请明确说明"根据现有知识库内容无法完全回答此问题"
3. 回答要简洁、准确，直接给出答案
4. 如有具体数据或步骤，列出关键信息
5. **必须**在答案最后，列出所有参考的文档来源（使用上述文档链接）

**输出格式：**
答案：[直接回答问题]
参考文档：[列出文档标题和链接]

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


def output_filter(question, answer):
    """LLM 输出一致性检查（快速模式）

    使用简短 prompt 判断答案是否回答了问题。
    如果答案与问题无关，标记 score=0，触发重新生成。

    返回: (is_relevant, score) - is_relevant=True 表示通过
    """
    if not MINIMAX_API_KEY:
        return True, 1.0

    if not answer or len(answer) < 10:
        return False, 0.0

    prompt = f"""判断以下答案是否回答了问题。

问题: {question}
答案: {answer}

判断规则：
- 如果答案与问题相关且有价值，返回 YES
- 如果答案与问题无关、答非所问，返回 NO
- 如果答案承认无法回答问题且有参考价值，返回 YES

输出格式：只输出 YES 或 NO，不要其他内容。"""

    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 10,
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
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get("content"):
                response_text = result["content"][0]["text"].strip().upper()
                is_relevant = "YES" in response_text
                score = 1.0 if is_relevant else 0.0
                return is_relevant, score
    except:
        pass

    # API 失败时默认通过，避免阻塞
    return True, 1.0


def main():
    parser = argparse.ArgumentParser(description="飞书知识库问答")
    parser.add_argument("--question", "-q", required=True, help="用户问题")
    parser.add_argument("--system", "-s", default="auto", help="系统: 低延时|顶点|all|auto (默认 auto)")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="最大检索文档数")
    parser.add_argument("--api-key", "-k", default=None, help="MiniMax API Key")

    args = parser.parse_args()

    if args.api_key:
        os.environ["MINIMAX_API_KEY"] = args.api_key

    # 输入安全过滤
    passed, reason = input_filter(args.question)
    if not passed:
        print(f"[安全拦截] {reason}")
        return

    # 自动检测系统
    if args.system == "auto":
        detected = detect_system(args.question)
        print(f"[检测] 系统: {detected}")
    else:
        detected = args.system if args.system != "auto" else "all"

    print(f"[问题] {args.question}")
    print(f"[系统] {detected}")
    print()

    # 搜索（关键词 + 向量混合检索）
    print("[搜索] 调用 lark-cli 搜索...")
    results = search_with_hybrid(args.question, detected, args.max_results)

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
        content, error = fetch_document_content(doc["token"], doc["doc_type"], doc.get("file_type", ""))
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

    # 输出一致性检查
    is_relevant, score = output_filter(args.question, answer)
    if not is_relevant:
        print("[输出一致性检查] 答案与问题不相关，重新生成...")
        answer, error = generate_answer(args.question, context_text)
        if error:
            print(f"[错误] 重新生成失败: {error}")
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