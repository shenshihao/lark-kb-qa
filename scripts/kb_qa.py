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

    print(f"🔍 正在搜索知识库: {args.question}")
    print(f"   系统: {args.system}")
    print()

    # Step 1: 搜索
    results, error = search_knowledge_base(args.question, args.system, args.max_results)
    if error:
        print(f"❌ {error}")
        return

    if not results:
        print("❌ 未找到相关文档")
        return

    print(f"✅ 找到 {len(results)} 篇相关文档\n")

    # Step 2: 获取内容
    print("📄 正在获取文档内容...\n")
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
        print("❌ 无法获取文档内容")
        return

    # Step 3: 生成答案
    print("\n🤖 正在生成答案...\n")

    context_text = "\n\n".join(contexts)
    answer, error = generate_answer(args.question, context_text)

    if error:
        print(f"❌ {error}")
        print("\n--- 参考文档 ---")
        print("\n".join(sources))
        return

    # 输出结果
    print("=" * 50)
    print("📝 答案:")
    print("=" * 50)
    print(answer)
    print()
    print("=" * 50)
    print("📚 参考文档:")
    print("=" * 50)
    print("\n".join(sources))


if __name__ == "__main__":
    main()
