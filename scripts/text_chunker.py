#!/usr/bin/env python3
"""
text_chunker.py: 文本分块模块
按段落/标题分块，每块 500-1000 字
"""

import re
from typing import List, Tuple


# 配置
MIN_CHUNK_SIZE = 300      # 最小块大小（不足时与相邻块合并）
MAX_CHUNK_SIZE = 1000     # 最大块大小
CHUNK_OVERLAP = 50        # 块之间的重叠字数（保持上下文连贯）

# 标题模式（匹配 H1/H2/H3 等）
TITLE_PATTERNS = [
    r'^#{1,6}\s+(.+)$',           # Markdown 标题
    r'^(\d+[\.、]\s*[^\n]+)$',    # 数字编号标题：1. xxx 或 1、xxx
    r'^([A-Z][A-Z0-9\s]{0,30})$',  # 全大写标题
    r'^【(.+?)】$',               # 方括号标题：【xxx】
    r'^(.{2,20})[:：]$',          # 冒号结尾的标题
]


def is_title(line: str) -> bool:
    """判断行是否为标题"""
    line = line.strip()
    if not line:
        return False
    for pattern in TITLE_PATTERNS:
        if re.match(pattern, line):
            return True
    return False


def get_title_level(line: str) -> int:
    """获取标题级别（1=H1, 2=H2, ...）"""
    md_match = re.match(r'^(#{1,6})\s+', line)
    if md_match:
        return len(md_match.group(1))
    return 3  # 其他标题默认为 H3 级别


def split_by_paragraphs(text: str) -> List[str]:
    """按段落分割文本"""
    # 分割段落（多个换行合并为一个）
    paragraphs = re.split(r'\n\s*\n', text)
    return [p.strip() for p in paragraphs if p.strip()]


def split_by_headings(text: str) -> List[Tuple[str, str]]:
    """按标题分割文本，返回 [(title, content), ...]

    如果行不是标题，则 title 为空字符串
    """
    lines = text.split('\n')
    sections = []
    current_title = ""
    current_content = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if is_title(stripped):
            # 保存上一个 section
            if current_content:
                sections.append((current_title, '\n'.join(current_content)))
            current_title = stripped
            current_content = []
        else:
            current_content.append(stripped)

    # 保存最后一个 section
    if current_content:
        sections.append((current_title, '\n'.join(current_content)))

    return sections


def chunk_text(text: str, min_size: int = MIN_CHUNK_SIZE, max_size: int = MAX_CHUNK_SIZE) -> List[dict]:
    """将文本分块

    返回: [{"chunk_id": "doc_id_0", "title": "xxx", "content": "...", "is_title_chunk": 0/1}, ...]
    """
    # 先按标题分割
    sections = split_by_headings(text)

    chunks = []
    chunk_id_prefix = "chunk"

    for section_idx, (title, content) in enumerate(sections):
        if not content.strip():
            continue

        # 如果内容小于最小块大小，直接作为一块
        if len(content) < min_size:
            chunk = {
                "chunk_id": f"{chunk_id_prefix}_{section_idx}_0",
                "title": title,
                "content": content.strip(),
                "is_title_chunk": 1 if title and not content.strip() else 0
            }
            chunks.append(chunk)
            continue

        # 按句子/段落分割成更小的单元
        # 句子分割：按句号、感叹号、问号分割
        sentences = re.split(r'([。！？；\n])', content)

        # 合并句子成块
        current_chunk = ""
        sentence_idx = 0

        while sentence_idx < len(sentences):
            sent = sentences[sentence_idx]

            # 如果是分隔符（。！？；\n），直接附加
            if sent in '。！？；\n' and current_chunk:
                current_chunk += sent
                sentence_idx += 1
            else:
                # 如果加上这个句子会超过最大块大小
                if len(current_chunk) + len(sent) > max_size and current_chunk:
                    # 保存当前块
                    chunk = {
                        "chunk_id": f"{chunk_id_prefix}_{section_idx}_{len(chunks)}",
                        "title": title if len(chunks) == 0 or not title else "",  # 只有第一个块保留标题
                        "content": current_chunk.strip(),
                        "is_title_chunk": 1 if title and len(chunks) == 0 else 0
                    }
                    chunks.append(chunk)

                    # 开始新块，保留一定重叠
                    overlap = current_chunk[-CHUNK_OVERLAP:] if len(current_chunk) > CHUNK_OVERLAP else ""
                    current_chunk = overlap + sent
                else:
                    current_chunk += sent

                sentence_idx += 1

        # 保存最后一块
        if current_chunk.strip():
            # 如果最后一块太小，合并到上一个块
            if chunks and len(current_chunk) < min_size:
                chunks[-1]["content"] += "\n" + current_chunk.strip()
            else:
                chunk = {
                    "chunk_id": f"{chunk_id_prefix}_{section_idx}_{len(chunks)}",
                    "title": "",  # 后续块不保留标题
                    "content": current_chunk.strip(),
                    "is_title_chunk": 0
                }
                chunks.append(chunk)

    return chunks


def chunk_document(doc_id: str, doc_title: str, content: str, doc_type: str = "native") -> List[dict]:
    """将文档内容分块，带文档 ID 前缀

    Args:
        doc_id: 文档 ID
        doc_title: 文档标题
        content: 文档内容
        doc_type: 文档类型 (native/excel/word/pdf/ppt)

    Returns:
        [(chunk_id, doc_id, chunk_index, title, content, is_title_chunk), ...]
    """
    chunks = chunk_text(content)

    result = []
    for idx, chunk in enumerate(chunks):
        result.append((
            f"{doc_id}_{idx}",
            doc_id,
            idx,
            chunk["title"] or doc_title,  # 如果没有标题，用文档标题
            chunk["content"],
            chunk["is_title_chunk"]
        ))

    return result


def chunk_excel_content(sheet_name: str, rows: List[List[str]], doc_id: str) -> List[Tuple]:
    """将 Excel 内容分块

    Args:
        sheet_name: 工作表名称
        rows: 表格行数据
        doc_id: 文档 ID

    Returns:
        [(chunk_id, doc_id, chunk_index, title, content, is_title_chunk), ...]
    """
    chunks = []
    chunk_id_prefix = f"{doc_id}_sheet"

    # 工作表名作为标题块
    title_chunk = (
        f"{chunk_id_prefix}_0",
        doc_id,
        0,
        sheet_name,
        f"工作表：{sheet_name}",
        1
    )
    chunks.append(title_chunk)

    # 将行数据转为文本
    current_chunk_content = ""
    content_start_idx = 1

    for row_idx, row in enumerate(rows):
        # 过滤 None 值
        row_text = " | ".join(str(cell) for cell in row if cell is not None)
        if not row_text.strip():
            continue

        row_text = f"第{row_idx + 1}行: {row_text}"

        # 检查是否需要分块
        if len(current_chunk_content) + len(row_text) > MAX_CHUNK_SIZE and current_chunk_content:
            chunk = (
                f"{chunk_id_prefix}_{len(chunks)}",
                doc_id,
                len(chunks),
                sheet_name,
                current_chunk_content.strip(),
                0
            )
            chunks.append(chunk)
            current_chunk_content = ""

        current_chunk_content += row_text + "\n"

    # 保存最后一块
    if current_chunk_content.strip():
        chunk = (
            f"{chunk_id_prefix}_{len(chunks)}",
            doc_id,
            len(chunks),
            sheet_name,
            current_chunk_content.strip(),
            0
        )
        chunks.append(chunk)

    return chunks


def main():
    """测试分块功能"""
    print("=== 文本分块测试 ===\n")

    # 测试用文档
    test_text = """
# 低延时系统使用指南

## 一、系统简介

低延时系统是公司自主研发的高性能交易系统，提供低延迟、高吞吐量的交易通道。系统支持多种接入方式，包括直连、统一接入和三方接入。

## 二、接入配置

### 2.1 直连配置

直连方式适用于机构客户，需要配置以下参数：
- 接入地址：101.231.93.226:32300
- 客户号：XXXXXX
- 资金账号：XXXXXX
- 密码：XXXXXX

### 2.2 统一接入配置

统一接入适用于零售客户，需要通过统一接入网关访问。

## 三、委托方式

低延时委托方式采用小写 o，具体规则如下：
1. 市价委托
2. 限价委托

## 四、常见问题

### 4.1 连接超时

如果遇到连接超时，请检查网络配置。

### 4.2 委托失败

委托失败可能原因：
- 资金不足
- 持仓不足
- 账户状态异常
"""

    print("原始文本长度:", len(test_text), "字符\n")

    chunks = chunk_text(test_text)
    print(f"分块数量: {len(chunks)}\n")

    for i, chunk in enumerate(chunks):
        print(f"--- 块 {i + 1} ---")
        print(f"标题: {chunk['title'] or '(无)'}")
        print(f"长度: {len(chunk['content'])} 字符")
        print(f"内容预览: {chunk['content'][:100]}...")
        print()


if __name__ == "__main__":
    main()