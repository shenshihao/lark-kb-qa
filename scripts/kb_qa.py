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

# 搜索缓存 (TTL 5分钟)
_SEARCH_CACHE = {}
_CACHE_TTL = 300


def _get_cache_key(query, system, max_results):
    return f"{query}|{system}|{max_results}"


def _get_cached(key):
    import time
    if key in _SEARCH_CACHE:
        timestamp, data = _SEARCH_CACHE[key]
        if time.time() - timestamp < _CACHE_TTL:
            return data
        del _SEARCH_CACHE[key]
    return None


def _set_cache(key, data):
    import time
    _SEARCH_CACHE[key] = (time.time(), data)


def _clear_expired_cache():
    import time
    keys_to_delete = []
    for key, (timestamp, _) in _SEARCH_CACHE.items():
        if time.time() - timestamp >= _CACHE_TTL:
            keys_to_delete.append(key)
    for key in keys_to_delete:
        del _SEARCH_CACHE[key]


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

def html_escape(text):
    """安全转义 HTML 特殊字符，防止 XSS"""
    if not text:
        return ""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;"))


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
    # 支持 list 或 str，list 时使用 shell=False 防止注入
    if isinstance(cmd, list):
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    else:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
            text, err = parse_llm_response(result)
            if not err:
                queries = text.strip().split('\n')
                return [q.strip() for q in queries if q.strip()][:6]
    except:
        pass

    return expand_query_fallback(question)


def expand_query_fallback(question):
    """备用扩展逻辑"""
    queries = [question]
    q_lower = question.lower()

    # 对数字代码补充上下文（支持负数如 -122）
    numbers = re.findall(r'-?\d+', question)
    for num in numbers:
        if len(num) >= 3:  # 至少3位数字
            queries.extend([
                f"{num}",
                f"错误码 {num}",
                f"接口 {num}",
            ])

    # 对wtfs补充
    if "wtfs" in q_lower:
        queries.extend([
            "wtfs 委托方式",
            "委托方式 wtfs",
            "wtfs",
        ])

    # HTS 实体词扩展
    if re.search(r'hts[h]?', q_lower):
        queries.extend([
            "HTS",
            "HTS两融",
            "顶点HTS",
        ])

    # 两融 实体词扩展
    if "两融" in question:
        queries.extend([
            "两融",
            "融资融券",
            "融资开仓",
        ])

    # 上场/下场 实体词扩展
    if re.search(r'[上下]场', question):
        queries.extend([
            "上场文件",
            "下场文件",
            "两融上下场",
        ])

    # 负债合约/合约 实体词扩展
    if "负债合约" in question or "合约" in question:
        queries.extend([
            "负债合约",
            "客户合约",
            "两融合约",
        ])

    # symbol 实体词扩展
    if "symbol" in q_lower:
        queries.extend([
            "symbol",
            "合约",
            "HT符号",
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


def get_empty_result_suggestions(question):
    """为空结果时生成用户建议"""
    suggestions = []

    # 基于问题词推荐相关搜索词
    stop_words = {"的", "了", "是", "在", "和", "与", "或", "及", "等", "我", "你", "他", "她", "它", "这", "那"}
    words = re.findall(r'[\w]+', question)
    main_terms = [w for w in words if w not in stop_words and len(w) >= 2]

    if main_terms:
        # 提取主词
        core = main_terms[0] if main_terms else ""
        suggestions.append(f"可以尝试搜索「{core}规则」或「{core}说明」")

    # 基于系统关键词推荐
    question_lower = question.lower()
    if any(t.lower() in question_lower for t in ["融资", "融券", "开仓", "平仓"]):
        suggestions.append("可以尝试搜索「融资融券规则」或「两融业务说明」")
    if any(t.lower() in question_lower for t in ["保证金", "征信", "授信"]):
        suggestions.append("可以尝试搜索「保证金规则」或「征信的流程」")
    if any(t.lower() in question_lower for t in ["风控", "预警", "平仓线"]):
        suggestions.append("可以尝试搜索「风控规则」或「预警线说明」")

    if not suggestions:
        suggestions.append("可以尝试使用更通用的关键词，如业务名称或系统名称")

    return " ".join(suggestions)


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


def search_with_bm25(question, system="all", max_results=5):
    """BM25 本地检索

    使用 SQLite FTS5 BM25 全文索引检索文档。
    需要先通过 sync_wiki.py 建立索引。

    Args:
        question: 用户问题
        system: 系统分类 (低延时/顶点/all)
        max_results: 最大返回结果数

    Returns:
        [{title, url, token, doc_type, summary, score, source}, ...]
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    try:
        from scripts import bm25_index

        # 检查索引是否存在
        stats = bm25_index.get_stats()
        if stats["doc_count"] == 0:
            print("[BM25] 索引为空，请先运行 sync_wiki.py --full 建立索引")
            return []

        # 生成搜索词（复用现有 LLM 扩展逻辑）
        queries = generate_search_queries(question, system)
        print(f"[BM25] 搜索词: {queries[:3]}...")

        # 多路召回
        all_results = {}
        for query in queries:
            try:
                results = bm25_index.search_with_synonyms(query, top_k=max_results * 2)
                for r in results:
                    # r = (chunk_id, doc_id, chunk_index, title, content, score, doc_title, doc_url)
                    title = r[6] or r[3]  # doc_title or title
                    token = r[1]  # doc_id
                    url = r[7] or f"https://Feishu.cn/docx/{token}"  # doc_url

                    if title not in all_results or r[5] > all_results[title]["score"]:
                        all_results[title] = {
                            "title": title,
                            "url": url,
                            "token": token,
                            "doc_type": "native",
                            "summary": r[4][:200] if r[4] else "",  # content preview
                            "score": r[5],
                            "source": "bm25"
                        }
            except Exception as e:
                print(f"[BM25] 查询 '{query}' 失败: {e}")

        # 按分数排序
        merged = list(all_results.values())
        merged.sort(key=lambda x: x.get("score", 0), reverse=True)

        # 系统过滤（如果需要）
        if system != "all":
            system_kws = SYSTEM_TITLE_KEYWORDS.get(system, [])
            filtered = []
            for r in merged:
                matched = any(kw.lower() in r["title"].lower() for kw in system_kws)
                if matched:
                    filtered.append(r)
            merged = filtered

        print(f"[BM25] 找到 {len(merged)} 条结果")
        return merged[:max_results]

    except ImportError as e:
        print(f"[BM25] 模块导入失败: {e}")
        return []
    except Exception as e:
        print(f"[BM25] 检索失败: {e}")
        return []


def search_lark_cli(query, system="all", max_results=10):
    """调用 lark-cli 搜索知识库"""
    # 查询参数校验
    if not query or len(query.strip()) < 1:
        return [], "查询词无效"
    query = query.strip()[:200]  # 限制长度，防止过长输入

    # 检查缓存
    _clear_expired_cache()
    cache_key = _get_cache_key(query, system, max_results)
    cached = _get_cached(cache_key)
    if cached is not None:
        print(f"[缓存命中] {query}")
        return cached, None

    cmd = ["lark-cli", "docs", "+search", "--query", query, "--page-size", str(max_results), "--format", "json"]
    stdout, stderr, code = run_command(cmd)

    if code != 0 or not stdout:
        return [], f"搜索失败: {stderr or '无输出'}"

    try:
        data = json.loads(stdout)
        results = []
        if data.get("ok") and data.get("data", {}).get("results"):
            for item in data["data"]["results"][:max_results]:
                meta = item.get("result_meta", {})

                # file_type 在顶层的可能是空，需要从 icon_info 中解析
                file_type = meta.get("file_type", "")
                if not file_type:
                    icon_info_str = meta.get("icon_info", "")
                    if icon_info_str:
                        try:
                            icon_info = json.loads(icon_info_str)
                            file_type = icon_info.get("file_type", "")
                        except:
                            pass

                # 标题优先取 title_highlighted（需要去除 HTML 标签），再回退到 title
                title = meta.get("title", "")
                if not title:
                    title_highlighted = item.get("title_highlighted", "")
                    if title_highlighted:
                        # 去除 HTML 标签
                        import re
                        title = re.sub(r'<[^>]+>', '', title_highlighted)

                results.append({
                    "title": html_escape(title) if title else "无标题",
                    "url": meta.get("url", "") or "",
                    "token": meta.get("token", "") or "",
                    "doc_type": meta.get("doc_types", "") or "",
                    "file_type": file_type or "",
                    "summary": html_escape(item.get("summary_highlighted", "")) if item.get("summary_highlighted") else "",
                })
        # 写入缓存
        _set_cache(cache_key, results)
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
        get_node_cmd = ["lark-cli", "wiki", "spaces", "get_node", "--params", f'{{"token":"{doc_token}"}}']
        stdout, stderr, code = run_command(get_node_cmd)
        if code == 0:
            try:
                node_data = json.loads(stdout)
                if node_data.get("ok"):
                    doc_token = node_data["data"]["node"]["obj_token"]
                    doc_type = node_data["data"]["node"]["obj_type"].upper()
                    # 只有当 node 返回的 file_type 不为空时才覆盖，避免丢失搜索结果中的 file_type
                    node_file_type = node_data["data"]["node"].get("file_type", "")
                    if node_file_type:
                        file_type = node_file_type
            except:
                pass

    if doc_type in ["DOCX", "DOC"]:
        cmd = ["lark-cli", "docs", "+fetch", "--doc", doc_token, "--format", "json"]
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
        ft_lower = file_type.lower()
        if ft_lower in ("xlsx", "xls"):
            return parse_document(doc_token, "XLSX", max_chars=1000)
        elif ft_lower == "pdf":
            return parse_document(doc_token, "PDF", max_chars=1000)
        elif ft_lower == "csv":
            return parse_document(doc_token, "CSV", max_chars=1000)
        elif ft_lower == "docx":
            # docx 走 docs +fetch
            cmd = ["lark-cli", "docs", "+fetch", "--doc", doc_token, "--format", "json"]
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


def parse_llm_response(result):
    """解析 LLM API 返回的响应，支持多种格式

    支持的格式：
    - {"content": [{"text": "答案"}]}  // 标准格式
    - {"content": "答案"}              // 直接字符串
    - {"text": "答案"}                  // text 字段
    - {"content": [{"type": "text", "text": "答案"}]}  // Anthropic 格式

    返回: (text, error_msg)
    """
    if not result:
        return None, "空响应"

    # 情况1: 标准格式 {"content": [{"text": "..."}]}
    if isinstance(result.get("content"), list) and result["content"]:
        first_item = result["content"][0]
        if isinstance(first_item, dict):
            # 支持多种内层格式
            text = first_item.get("text") or first_item.get("content")
            if text:
                return text, None
        elif isinstance(first_item, str):
            return first_item, None

    # 情况2: {"content": "直接字符串"}
    if isinstance(result.get("content"), str):
        return result["content"], None

    # 情况3: {"text": "答案"}
    if isinstance(result.get("text"), str):
        return result["text"], None

    # 情况4: Anthropic 格式 {"content": [{"type": "text", "text": "..."}]}
    if isinstance(result.get("content"), list) and result["content"]:
        for item in result["content"]:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if text:
                    return text, None

    return None, f"无法解析响应格式: {str(result)[:200]}"


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
            text, err = parse_llm_response(result)
            if err:
                return None, f"LLM 返回格式错误: {err}"
            return text, None
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
            text, err = parse_llm_response(result)
            if err:
                # 解析失败时默认通过，避免阻塞
                return True, 1.0
            response_text = text.strip().upper()
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
    parser.add_argument("--use-bm25", action="store_true", help="使用本地 BM25 检索（需先建立索引）")

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

    # 搜索
    if args.use_bm25:
        print("[搜索] 使用本地 BM25 检索...")
        results = search_with_bm25(args.question, detected, args.max_results)
    else:
        print("[搜索] 调用 lark-cli 搜索...")
        results = search_with_hybrid(args.question, detected, args.max_results)

    if not results:
        print("[未找到相关文档]")
        suggestions = get_empty_result_suggestions(args.question)
        print(f"[建议] {suggestions}")
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