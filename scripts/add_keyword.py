#!/usr/bin/env python3
"""
add_keyword.py: 添加用户自定义关键词
用法: python add_keyword.py "关键词"
"""

import json
import sys
from pathlib import Path

def load_keywords():
    kw_file = Path(__file__).parent.parent / "user_keywords.json"
    if kw_file.exists():
        return json.loads(kw_file.read_text(encoding="utf-8"))
    return []

def save_keywords(keywords):
    kw_file = Path(__file__).parent.parent / "user_keywords.json"
    kw_file.write_text(json.dumps(keywords, ensure_ascii=False, indent=2), encoding="utf-8")

def add_keyword(keyword):
    """添加关键词"""
    keywords = load_keywords()
    keyword = keyword.strip()

    if not keyword:
        return False, "关键词不能为空"

    if keyword in keywords:
        return False, f"关键词已存在: {keyword}"

    keywords.append(keyword)
    save_keywords(keywords)
    return True, f"已添加: {keyword}"

def remove_keyword(keyword):
    """删除关键词"""
    keywords = load_keywords()
    keyword = keyword.strip()

    if keyword not in keywords:
        return False, f"关键词不存在: {keyword}"

    keywords.remove(keyword)
    save_keywords(keywords)
    return True, f"已删除: {keyword}"

def list_keywords():
    """列出所有关键词"""
    keywords = load_keywords()
    return keywords

def main():
    if len(sys.argv) < 2:
        print("用法: python add_keyword.py <add|remove|list> [关键词]")
        print("示例: python add_keyword.py add '顶点合约'")
        print("示例: python add_keyword.py remove '顶点合约'")
        print("示例: python add_keyword.py list")
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "add":
        if len(sys.argv) < 3:
            print("错误: 请提供关键词")
            print("示例: python add_keyword.py add '顶点合约'")
            sys.exit(1)
        keyword = sys.argv[2]
        success, msg = add_keyword(keyword)
        print(msg)
        sys.exit(0 if success else 1)

    elif action == "remove":
        if len(sys.argv) < 3:
            print("错误: 请提供关键词")
            print("示例: python add_keyword.py remove '顶点合约'")
            sys.exit(1)
        keyword = sys.argv[2]
        success, msg = remove_keyword(keyword)
        print(msg)
        sys.exit(0 if success else 1)

    elif action == "list":
        keywords = list_keywords()
        print("当前用户关键词:")
        if not keywords:
            print("  (无)")
        else:
            for kw in keywords:
                print(f"  - {kw}")
        sys.exit(0)

    else:
        print(f"未知操作: {action}")
        print("用法: python add_keyword.py <add|remove|list> [关键词]")
        sys.exit(1)

if __name__ == "__main__":
    main()
