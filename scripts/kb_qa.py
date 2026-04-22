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

# 系统关键词映射
SYSTEM_KEYWORDS = {
    "低延时": "低延时",
    "顶点": "顶点",
}

# 查询扩展词库（近义词/相关概念）
QUERY_EXPANSIONS = {
    # 交易时段相关
    "交易时段": ["申报时间", "业务时段", "交易时间", "可交易时间", "禁止申报", "禁用时段"],
    "禁用": ["禁止", "不允许", "无法申报", "暂停"],
    "申报": ["委托", "下单", "交易申报"],

    # 折算率相关
    "折算率": ["折算比例", "折算系数", "担保比例", "折算"],
    "担保比例": ["维持担保比例", "风控比例", "平仓线", "警戒线"],

    # 资金相关
    "可用余额": ["可取余额", "资金余额", "账户余额", "可用资金"],
    "可取余额": ["可用余额", "资金余额", "可取资金"],

    # 交易相关
    "开仓": ["买入", "建仓", "融资开仓"],
    "平仓": ["卖出", "了结", "融资平仓"],
    "补仓": ["追保", "追加保证金"],
    "强平": ["强制平仓", "强行平仓"],

    # 错误相关
    "报错": ["错误", "失败", "异常", "问题"],
    "两融报错": ["融资报错", "融券报错", "两融错误", "融资融券异常"],
}

# 从错误信息中提取业务关键词
ERROR_KEYWORDS = [
    "交易时段", "业务申报", "禁用", "禁止", "申报限制",
    "融资", "融券", "保证金", "折算率", "授信", "额度"
]


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


def extract_business_terms(question):
    """从问题中提取业务关键词（用于扩展搜索）"""
    terms = []
    question_lower = question.lower()

    # 检查问题中是否包含已知的业务术语
    for key, synonyms in QUERY_EXPANSIONS.items():
        if key in question_lower or any(s in question_lower for s in synonyms):
            terms.append(key)
            terms.extend(synonyms)

    # 额外检查常见关键词
    for kw in ERROR_KEYWORDS:
        if kw in question_lower:
            terms.append(kw)

    return list(set(terms))


def expand_query(question):
    """扩展查询词，生成多个搜索查询"""
    queries = [question]  # 原始查询

    # 1. 处理错误信息场景
    if "报错" in question or "错误" in question or "禁用" in question or "禁止" in question:
        # 提取错误描述中的业务概念
        business_terms = extract_business_terms(question)
        for term in business_terms:
            if term not in queries:
                queries.append(term)

        # 专门处理"该交易时段内禁用此业务申报"类错误
        if "交易时段" in question or "申报" in question:
            queries.extend([
                "融资融券交易时段",
                "申报限制",
                "交易时段禁止申报",
                "融资申报时段",
                "融券申报时段",
                "两融交易规则",
                "融资融券业务规则"
            ])

    # 2. 添加通用扩展
    general_expansions = [
        "融资", "融券", "交易规则", "业务规则", "交易限制"
    ]
    for exp in general_expansions:
        if exp not in queries and (any(c.isalpha() and c < '一' for c in question)):
            # 如果问题是中文，添加扩展
            queries.append(exp)

    return queries[:6]  # 最多6个查询


def search_knowledge_base(query, system="all", max_results=5):
    """搜索知识库"""
    # 构建过滤条件
    if system != "all" and system in SYSTEM_KEYWORDS:
        query = f"{query} {SYSTEM_KEYWORDS[system]}"

    # 调用 lark-cli 搜索
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


def search_with_expansion(question, system="all", max_results=5):
    """带查询扩展的搜索"""
    queries = expand_query(question)

    all_results = {}  # 去重用 title 做 key

    print(f"[查询扩展] 尝试 {len(queries)} 个搜索词...")

    for q in queries:
        results, error = search_knowledge_base(q, system, max_results)
        if error:
            continue
        for r in results:
            if r["title"] not in all_results:
                all_results[r["title"]] = r

    return list(all_results.values()), None


def fetch_document_content(doc_token, doc_type):
    """获取文档内容"""
    # 处理 wiki 类型的 token
    if doc_type == "WIKI":
        # 需要先获取真实 token
        get_node_cmd = f'lark-cli wiki spaces get_node --params \'{{"token":"{doc_token}"}}\''
        stdout, stderr, code = run_command(get_node_cmd)
        if code == 0:
            try:
                node_data = json.loads(stdout)
                if node_data.get("ok"):
                    obj_token = node_data["data"]["node"]["obj_token"]
                    obj_type = node_data["data"]["node"]["obj_type"]
                    doc_token = obj_token
                    doc_type = obj_type.upper()
            except:
                pass

    # 获取文档内容
    if doc_type in ["DOCX", "DOC"]:
        cmd = f'lark-cli docs +fetch --doc {doc_token} --format json'
    else:
        return "", f"不支持的文档类型: {doc_type}"

    stdout, stderr, code = run_command(cmd)

    if code != 0:
        return "", f"获取文档失败: {stderr}"

    try:
        data = json.loads(stdout)
        if data.get("ok"):
            # 返回 markdown 内容（取前 4000 字符避免太长）
            content = data.get("data", {}).get("markdown", "")[:4000]
            return content, None
        else:
            return "", f"获取文档失败: {data.get('error', {})}"
    except json.JSONDecodeError as e:
        return "", f"解析文档内容失败: {e}"


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
            {
                "role": "user",
                "content": prompt
            }
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


def clean_markdown(text):
    """清理 markdown 中的高亮标签"""
    text = re.sub(r'</?h[^>]*>', '', text)  # 移除 <h> <hb> 等标签
    text = re.sub(r'</?b>', '', text)        # 移除 <b> </b> 标签
    text = re.sub(r'</?strong>', '', text)  # 移除 <strong>
    return text


def main():
    parser = argparse.ArgumentParser(description="飞书知识库问答")
    parser.add_argument("--question", "-q", required=True, help="用户问题")
    parser.add_argument("--system", "-s", default="all", help="系统: 低延时|顶点|all")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="最大检索文档数")
    parser.add_argument("--api-key", "-k", default=None, help="MiniMax API Key")

    args = parser.parse_args()

    # 设置 API Key
    if args.api_key:
        os.environ["MINIMAX_API_KEY"] = args.api_key

    print(f"[搜索] {args.question}")
    print(f"   [系统] {args.system}")
    print()

    # Step 1: 搜索（带扩展）
    results, error = search_with_expansion(args.question, args.system, args.max_results)
    if error:
        print(f"[错误] {error}")
        return

    if not results:
        print("[未找到相关文档]")
        return

    print(f"[找到 {len(results)} 篇相关文档]\n")

    # Step 2: 获取内容
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
            # 如果获取失败，用摘要
            if doc['summary']:
                contexts.append(f"【{doc['title']}】\n{doc['summary']}")
                sources.append(f"- {doc['title']}: {doc['url']}")

    if not contexts:
        print("[无法获取文档内容]")
        return

    # Step 3: 生成答案
    print("\n[生成答案]\n")

    context_text = "\n\n".join(contexts)
    answer, error = generate_answer(args.question, context_text)

    if error:
        print(f"[错误] {error}")
        print("\n--- 参考文档 ---")
        print("\n".join(sources))
        return

    # 输出结果
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