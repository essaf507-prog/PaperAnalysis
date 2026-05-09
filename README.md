# 📄 Paper Style Extractor — 学术论文风格提取与模板生成工具

> 爬取名校公开学术论文，提取写作风格与表述习惯，生成可复用的论文风格训练模板。
> 运行环境：Python 3.10+，IDEA 调试，Claude Code 辅助开发。

---

## 🗂 项目结构

```
paper_style_extractor/
│
├── main.py                  # 主入口：串联所有模块，提供验收出口
├── config.py                # 全局配置（API Key、延迟、代理、路径等）
├── requirements.txt         # 依赖清单
│
├── modules/
│   ├── scraper.py           # 【模块1】网页爬虫 + 反爬策略
│   ├── extractor.py         # 【模块2】正文提取 + 结构识别（摘要/正文/引用）
│   ├── chunker.py           # 【模块3】文本分块（Chunking）
│   ├── analyzer.py          # 【模块4】Claude 语义分析接口
│   └── template_generator.py# 【模块5】模板生成 + 输出格式化
│
├── output/                  # 分析结果输出目录（JSON + Markdown）
├── templates/               # 生成的风格模板存储目录
└── logs/                    # 运行日志
```

---

## 📦 模块职责说明

### 模块1 — `scraper.py` 爬虫 + 反爬
- **职责**：接收目标 URL（arXiv / ACL / Springer / IEEE 等），获取 HTML/PDF 页面内容
- **反爬策略**：
  - 随机 User-Agent 轮换（`fake_useragent`）
  - 请求间随机延迟（1~5s）
  - 支持代理池配置（HTTP/SOCKS5）
  - Retry 指数退避（最多 3 次重试）
  - Selenium fallback（处理 JS 渲染页面）
  - Robots.txt 合规检查
- **输出**：原始 HTML 字符串 / PDF bytes

---

### 模块2 — `extractor.py` 正文提取
- **职责**：从 HTML 或 PDF 中提取结构化论文内容
- **处理内容**：
  - 识别论文各部分：`Abstract`, `Introduction`, `Methodology`, `Results`, `Conclusion`, `References`
  - 清洗噪声（导航栏、广告、脚注、公式符号）
  - 支持 arXiv HTML、PDF 文本层提取（`pdfplumber`）
- **输出**：`{"abstract": "...", "sections": [{"title": "Introduction", "content": "..."}]}`

---

### 模块3 — `chunker.py` 文本分块
- **职责**：将长文本切分为适合 LLM 处理的 chunk
- **策略**：
  - 按段落 + 语义边界切分（不在句中截断）
  - 支持固定 token 窗口（默认 512 tokens）+ 滑动重叠（64 tokens）
  - 保留 section 上下文标签（每个 chunk 携带所在章节信息）
- **输出**：`[{"chunk_id": 1, "section": "Introduction", "text": "...", "tokens": 480}]`

---

### 模块4 — `analyzer.py` Claude 语义分析（核心）
- **职责**：调用 Claude API 对每个 chunk 进行多维度语义分析
- **分析维度**：
  1. **句式结构**：被动语态比例、从句使用习惯、句子平均长度
  2. **学术词汇**：高频学术词组、领域术语使用模式
  3. **论证逻辑**：递进/对比/让步转折词的使用频率与位置
  4. **段落组织**：段落开头/结尾的固定表述句式
  5. **引用风格**：文内引用格式与嵌入表达方式
- **接口形式**：`analyze_chunk(chunk_text, section_type) → StyleFeatures`
- **输出**：结构化 JSON，每个 chunk 的风格特征向量

---

### 模块5 — `template_generator.py` 模板生成
- **职责**：汇总所有 chunk 的风格特征，生成可复用的写作模板
- **输出内容**：
  1. **通用句式模板库**：按章节分类的高频句型（含占位符）
  2. **写作习惯报告**：该论文/作者的表述特点摘要
  3. **训练提示词**：可直接用于训练自己写作的 Prompt 模板
  4. **Markdown 文档**：人类可读的风格指南
- **输出文件**：`templates/style_template_<paper_id>.md` + `.json`

---

### `main.py` 主入口 + 验收模块
- **职责**：串联全流程，提供 CLI 接口与验收出口
- **使用方式**：
  ```bash
  python main.py --url "https://arxiv.org/abs/2310.xxxxx" --output ./output
  ```
- **验收出口**：
  - `output/<paper_id>/raw.html` — 原始爬取内容
  - `output/<paper_id>/extracted.json` — 结构化正文
  - `output/<paper_id>/chunks.json` — 分块结果
  - `output/<paper_id>/analysis.json` — 语义分析结果
  - `templates/style_template_<paper_id>.md` — 最终风格模板（核心交付物）
  - `logs/run_<timestamp>.log` — 完整运行日志

---

## ⚙️ 配置说明（`config.py`）

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API Key（必填） | 从环境变量读取 |
| `CLAUDE_MODEL` | 使用的模型 | `claude-sonnet-4-20250514` |
| `CHUNK_SIZE` | 每块最大 token 数 | `512` |
| `CHUNK_OVERLAP` | chunk 滑动重叠 token | `64` |
| `REQUEST_DELAY_MIN` | 最小请求间隔（秒） | `1.5` |
| `REQUEST_DELAY_MAX` | 最大请求间隔（秒） | `4.5` |
| `MAX_RETRIES` | 爬虫最大重试次数 | `3` |
| `USE_PROXY` | 是否启用代理 | `False` |
| `PROXY_LIST` | 代理地址列表 | `[]` |
| `USE_SELENIUM` | JS渲染页面时启用 | `False` |

---

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（推荐环境变量）
export ANTHROPIC_API_KEY="sk-ant-..."

# 3. 运行（arXiv 论文示例）
python main.py --url "https://arxiv.org/abs/2310.06825"

# 4. 查看结果
cat templates/style_template_*.md
```

---

## 🎯 支持的论文来源

| 来源 | 类型 | 说明 |
|---|---|---|
| arXiv.org | HTML + PDF | 优先使用 HTML 版本 |
| ACL Anthology | HTML | NLP 领域论文 |
| Semantic Scholar | HTML | 通用学术搜索 |
| PubMed | HTML | 生物医学 |
| 通用 URL | HTML | 任意公开页面 |

---

## 📋 验收清单

运行成功后，`output/` 目录应包含以下文件：

- [ ] `raw.html` — 原始页面已爬取
- [ ] `extracted.json` — 论文结构已解析（含 abstract + sections）
- [ ] `chunks.json` — 文本已分块（每块 ≤512 tokens）
- [ ] `analysis.json` — Claude 已完成语义分析
- [ ] `style_template_*.md` — **核心交付物**：风格模板已生成

---

## ⚠️ 使用说明

- 本工具仅用于**学习研究目的**，请遵守目标网站的 `robots.txt` 与使用条款
- 默认启用请求限速，请勿修改为过高频率
- Claude API 调用会产生费用，大批量处理前请估算 token 用量
