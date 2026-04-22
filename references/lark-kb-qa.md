# kb +qa（知识库问答）

> **前置条件：** 先阅读 [`../lark-shared/SKILL.md`](../lark-shared/SKILL.md) 了解认证。

基于知识库的 RAG 问答。当用户提问时：
1. 搜索知识库相关文档
2. 获取文档内容摘要
3. 结合 LLM 生成答案

## 命令

```bash
# 知识库问答
lark-cli kb +qa --question "低延时的撮合规则是什么"

# 指定系统
lark-cli kb +qa --question "融资开仓的流程" --system 顶点

# 只搜索标题（更精确但可能遗漏）
lark-cli kb +qa --question "接口调用实例" --only-title
```

## 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--question` | 是 | 用户问题 |
| `--system` | 否 | 系统分类：`低延时`、`顶点`、`all`（默认 all） |
| `--only-title` | 否 | 是否只搜索标题（默认 false） |
| `--max-results` | 否 | 最大检索文档数（默认 5） |

## 输出格式

返回：
- **答案**：基于检索内容生成的答案
- **来源**：相关文档链接列表

## 实现流程

1. **关键词提取**：从问题中提取关键词
2. **知识库搜索**：调用 `docs +search` 搜索相关文档
3. **内容获取**：对相关文档调用 `docs +fetch` 获取内容摘要
4. **Prompt 构建**：将问题和检索内容组合成 Prompt
5. **LLM 调用**：调用 MiniMax API 生成答案
6. **结果返回**：返回答案和来源链接

## LLM Prompt 模板

```
你是知识库问答助手。请根据以下检索到的知识库内容，回答用户的问题。

**用户问题：**
{question}

**检索到的知识库内容：**
{context}

**要求：**
1. 基于检索内容回答，不要编造答案
2. 如果检索内容不足以回答，请说明"根据现有知识库内容无法完全回答此问题"
3. 回答要简洁、准确
4. 如有需要，可以列举具体的数据或步骤

**答案：**
```

## 知识库文档类型

| 类型 | doc_types | 说明 |
|------|-----------|------|
| Word 文档 | `DOCX` | .docx 文件 |
| Excel 表格 | `SHEET` | .xlsx 文件 |
| Wiki 节点 | `WIKI` | 知识库节点 |

## 示例

**用户问题：** 低延时撮合规则是什么？

**检索结果：**
- 低延时问题总结.docx（相关内容摘要）
- 接口调用实例.docx（相关内容摘要）

**生成答案：**
根据检索到的知识库内容，低延时撮合规则如下：
- 2000：不成交
- 3000：部分成交
- 5000：完全成交（分三笔成交）
- 6000：废单
- 其他任意数量：完全成交

**来源：**
- [低延时问题总结.docx](https://dcndc1g978pn.feishu.cn/wiki/xxx)

## 注意事项

- 检索内容可能不完整，LLM 回答时需要说明来源
- Excel 表格内容通过 sheet 相关 API 获取
- 如果搜索结果为空，会返回"未找到相关文档"

## 参考

- [lark-kb-search](../lark-kb-search/SKILL.md) -- 知识库搜索
- [lark-doc-search](../lark-doc/references/lark-doc-search.md) -- 底层搜索能力
- [lark-doc-fetch](../lark-doc/references/lark-doc-fetch.md) -- 文档内容获取
- [lark-shared](../lark-shared/SKILL.md) -- 认证和全局参数
