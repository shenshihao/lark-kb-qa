---
name: lark-kb-qa
version: 3.2.0
description: "飞书知识库问答：基于知识库文档的 RAG 问答。当用户提问业务问题、技术问题时，先检索知识库文档，再结合 LLM 生成答案。"
trigger_keywords: "知识库、查询、怎么用、如何、是什么、多少、哪里、请问、教我、为什么、怎么查、帮我查查、看一下、看看、帮我看看、规则、条件、流程、说明、手续、限制、禁止、禁用、报错、错误、失败、异常、问题、如何办、怎么办、如何处理、如何解决、融资、融券、开仓、平仓、补仓、追保、交易、买入、卖出、认购、申购、赎回、委托、下单、撤单、改单、账户、账号、席位、保证金、征信、利息、负债、合约、净资产、授信、额度、风控、预警、平仓线、警戒线、维持担保比例、折算率、折算比例、担保比例、可用余额、可取余额、资金余额、现金、股份、持仓、市值、低延时、顶点、撮合、symbol、agw、柜台、节点、席位号、交易时段、申报、业务时段、禁止申报、禁用、顶点现货、顶点期货"
metadata:
  requires:
    bins: ["lark-cli", "python3"]
  cliHelp: "python scripts/kb_qa.py --help"
---

# lark-kb-qa（知识库问答）

**核心能力：** RAG（检索增强生成）问答。接收用户问题，检索知识库文档，结合 LLM 生成答案。

## 核心特性

### 1. 系统自动路由
根据问题关键词自动判断属于哪个知识库系统：
- **低延时**：低延时、lowlatency 相关问题
- **顶点**：顶点、现货、期货、symbol 相关问题
- **全局**：其他问题，搜索全部知识库

### 2. LLM 驱动查询扩展
不再依赖预定义近义词，而是：
1. 将问题发给 LLM
2. LLM 生成 3-6 个不同角度的搜索词
3. 并行搜索，合并去重

**示例**：
- 用户问"7100701是什么接口" → LLM 生成：["7100701 接口", "7100701 symbol", "接口 7100701", "7100701 合约"]
- 用户问"折算率是多少" → LLM 生成：["折算率", "折算比例", "担保比例", "维持担保比例"]

### 3. 本地 BM25 检索（SQLite FTS5）
- 中文查询分词：按字符边界分割中英文混合查询
- LIKE 模糊搜索 fallback：FTS5 失败时使用
- OR 评分逻辑：任一词匹配，按评分排序

### 4. 文件附件全文索引
支持同步和检索以下文件类型：
- Excel (.xlsx, .xls)
- Word (.docx, .doc)
- PDF (.pdf)
- PPT (.pptx, .ppt)
- CSV (.csv)

## 触发关键词

当用户消息包含以下关键词时，系统自动触发此 Skill：
- 通用疑问：知识库、查询、怎么用、如何、是什么、多少、哪里、请问、教我、为什么、怎么查、帮我查查、看一下、看看、帮我看看
- 业务操作：规则、条件、流程、说明、手续、限制、禁止、禁用
- 故障排除：报错、错误、失败、异常、问题、错误码、如何办、怎么办、如何处理、如何解决
- 交易相关：融资、融券、开仓、平仓、补仓、追保、交易、买入、卖出、认购、申购、赎回、委托、下单、撤单、改单
- 账户相关：账户、账号、席位、保证金、征信、利息、负债、合约、净资产、授信、额度、资金余额、可用余额、可取余额、现金、股份、持仓、市值
- 风险管理：风控、预警、平仓线、警戒线、维持担保比例、折算率、折算比例、担保比例
- 系统名称：低延时、顶点、撮合、symbol、agw、柜台、节点、席位号、委托方式、报盘、client_feature、lowlatency、hts、HT符号
- 交易时段：交易时段、申报、业务时段、禁止申报、禁用
- 顶点系统：顶点现货、顶点期货
- 交易相关：市价、限价、委托、订单、仓位
- 风控相关：强行平仓、强制平仓、爆仓

## 工作流程

```
用户问题 → 系统路由判断 → LLM 生成多角度搜索词 → 并行搜索 → 文档内容获取 → LLM 生成答案 → 返回答案+来源
```

## 安装

```bash
# 克隆代码
git clone https://github.com/shenshihao/lark-kb-qa.git
cd lark-kb-qa

# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export MINIMAX_API_KEY="your-minimax-api-key"

# 可选：建立本地向量索引（加速语义搜索）
python scripts/embedding_cache.py --build
```

## 本地 BM25 索引

### 初始化索引（全量同步）

```bash
# 同步整个知识库到本地 SQLite
python scripts/sync_wiki.py --full

# 查看索引状态
python scripts/sync_wiki.py --stats
```

**输出示例**：
```
=== 索引统计 ===
文档数: 23
块数: 499
同义词数: 0
数据库大小: 1.03 MB
```

### 增量同步单个文档

```bash
# 同步前先通过 lark-cli 搜索确认文档 token
lark-cli docs +search --query "文档标题关键词"

# 增量同步（会自动覆盖已有文档）
python scripts/sync_wiki.py --doc <doc_token>
```

### 索引新增文档的正确流程

当知识库新增文档后，需要手动同步到本地索引：

1. **搜索文档**：知道文档标题后，用 `lark-cli docs +search` 找到 doc_token
2. **同步文档**：用 `sync_wiki.py --doc <doc_token>` 增量同步
3. **验证**：用 `sync_wiki.py --stats` 确认文档数增加

**示例**：
```bash
# 1. 找到文档 token
lark-cli docs +search --query "低延时问题总结"
# 返回: token: "ZzQNdBDw9ogRvYx7sVpcQcv5npg"

# 2. 同步到本地索引
python scripts/sync_wiki.py --doc ZzQNdBDw9ogRvYx7sVpcQcv5npg

# 3. 验证
python scripts/sync_wiki.py --stats
```

### 手动添加文档到索引（通过 Python）

```python
import sys, os
sys.path.insert(0, '.')
from scripts import bm25_index, text_chunker

# 获取文档内容（通过 lark-cli 或 API）
doc_id = "你的文档ID"
doc_title = "文档标题"
doc_url = "https://feishu.cn/docx/xxx"
content = "文档内容文本..."

# 分块
chunks = text_chunker.chunk_document(doc_id, doc_title, content, "native")

# 添加到索引
bm25_index.add_doc(doc_id=doc_id, doc_title=doc_title, doc_type="native", doc_url=doc_url)
bm25_index.delete_doc_chunks(doc_id)  # 删除旧块（如有）
bm25_index.add_chunks(chunks)

print(f"已索引 {len(chunks)} 个分块")
```

### 查询测试

```bash
# 本地搜索测试
python scripts/kb_qa.py --question "顶点503错误码" --use-bm25

# 不使用本地索引，直接搜索飞书
python scripts/kb_qa.py --question "顶点503错误码"
```

## 向量检索（可选）

启用向量检索可提升语义匹配效果：

1. 注册 Jina AI (https://jina.ai) 获取免费 API Key
2. 设置 `JINA_API_KEY` 环境变量
3. 运行 `python scripts/embedding_cache.py --build` 建立向量索引

**优势：** 能找到表达不同但语义相似的文档（如"加杠杆"匹配"融资买入"）

## 使用方法

用户直接提问即可自动触发，例如：
- "7100701是什么接口？"（自动补充业务上下文）
- "折算率是多少？"
- "融资开仓流程是怎样的？"
- "顶点现货的交易规则是什么？"
- "两融报错该交易时段内禁用此业务申报是什么意思？"

## 脚本参数

| 参数 | 说明 |
|------|------|
| `--question` | 用户问题（从用户消息自动提取） |
| `--system` | 系统分类：`低延时`、`顶点`、`all`、`auto`（默认 auto） |
| `--max-results` | 最大检索文档数（默认 5） |
| `--api-key` | MiniMax API Key（也可通过环境变量 `MINIMAX_API_KEY` 设置） |
| `--index` | 本地索引文件路径（可选，用于加速） |
| `--use-bm25` | 使用本地 BM25 检索（需先运行 sync_wiki.py --full 建立索引） |

## 输出格式

脚本返回格式化的文本答案，包含：
- 答案内容
- 参考文档链接

## 依赖

- Python 3.6+
- lark-cli（已配置好认证）
- requests（HTTP 调用）
- PyPDF2（PDF 解析，可选）
- openpyxl（Excel 解析，可选）
- pandas（CSV 解析，可选）
- python-docx（Word 解析，可选）
- pdfplumber（PDF 深度解析，可选）
- python-pptx（PPT 解析，可选）
- MiniMax API Key（用于 LLM 查询扩展和答案生成）
- Jina API Key（可选，用于向量检索）
- SQLite FTS5（内置，无需安装，用于 BM25 检索）

## 知识库配置

| 配置 | 值 |
|------|-----|
| 知识库 | 星河（space_id: 7628219860123667634） |
| LLM 模型 | MiniMax-M2.7 |
| LLM API | https://api.minimaxi.com/anthropic |

## 架构

```
lark-kb-qa/
├── SKILL.md                      # 本文档
├── requirements.txt              # Python 依赖
├── agent/
│   └── SOUL.md                   # Agent 角色定义
├── references/
│   └── lark-kb-qa.md            # 参考资料
└── scripts/
    ├── kb_qa.py                 # 主问答脚本
    ├── bm25_index.py            # BM25 索引模块（SQLite FTS5）
    ├── text_chunker.py          # 文本分块模块
    ├── doc_parser.py            # 文档解析模块（Excel/Word/PDF/PPT）
    ├── sync_wiki.py             # Wiki 同步脚本
    ├── embedding_cache.py      # 向量索引（可选）
    └── scan_knowledge_base.py   # 索引扫描脚本（旧版）
```

### 核心模块

| 模块 | 职责 |
|------|------|
| `bm25_index.py` | SQLite FTS5 全文索引：添加/删除文档、分块、搜索 |
| `text_chunker.py` | 按段落/标题分块（500-1000字/块），提取标题作为块标题 |
| `doc_parser.py` | 解析 Excel/Word/PDF/PPT，提取纯文本内容 |
| `sync_wiki.py` | 遍历知识库节点，下载文件并建立索引 |
| `kb_qa.py` | 主流程：路由+LLM扩展+检索+生成答案 |

### BM25 搜索逻辑

```
search_like(query)
  → 分词：re.findall 中文+英文分割
  → SQL LIKE (OR 逻辑，任一匹配)
  → Python 评分排序
  → 返回 top_k 结果
```

### 新增文档索引流程

```
发现新文档
  → lark-cli docs +search 找到 token
  → lark-cli docs +fetch 获取内容
  → text_chunker.chunk_document 分块
  → bm25_index.add_doc + add_chunks
  → 完成
```

## 常见问题

### Q: 搜索"顶点503"找不到结果
A: 中文查询会被分割为 ["顶点", "503"]，在数据库中搜索任一词匹配的内容。内容已确认存在：`HTS_ERR_AlreadyExistTradeAcc | -503 | 交易账户已存在`

### Q: 新增文档后本地索引搜不到
A: 需要手动执行 `python scripts/sync_wiki.py --doc <doc_token>` 增量同步。详见上方"索引新增文档的正确流程"

### Q: FTS5 检索失败怎么办
A: `search_like()` 会作为 fallback 自动启用，使用 LIKE 模糊搜索，不依赖 FTS5

### Q: 如何清空索引
A: `python scripts/sync_wiki.py --clear`，然后输入 `yes` 确认

## 权限

| 操作 | 所需 scope |
|------|-----------|
| 搜索云空间对象 | `search:docs:read` |
| 读取文档内容 | `docx:document:read` |

## 安全特性

### 命令注入防护
- 所有 `lark-cli` 调用使用 list 参数格式，避免 shell 注入
- query 参数限制长度（200字符）并去除首尾空格

### XSS 防护
- 搜索结果中的 title、summary 等字段自动 HTML 转义
- 防止搜索结果中的恶意脚本注入

### 输入校验
- 内置攻击模式检测（提示词泄露、角色扮演、标签注入等）
- 短问题拦截（小于2字符）

### 搜索缓存
- 同一查询词 5 分钟内重复搜索直接返回缓存
- 减少 API 调用次数，避免限速

