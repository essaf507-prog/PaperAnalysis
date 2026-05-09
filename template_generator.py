# modules/template_generator.py — 模块5：风格模板生成
#
# 职责：汇总所有 chunk 的分析结果，生成可复用的写作风格模板
# 输出：Markdown 风格指南 + JSON 结构化模板
# ─────────────────────────────────────────────────────────────────

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Dict
from datetime import datetime

import anthropic

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 特征聚合
# ──────────────────────────────────────────────

def aggregate_features(analyzed_chunks: List[dict]) -> Dict:
    """
    从所有 chunk 的分析结果中聚合共同风格特征

    策略：
    - 句式模板：按频率去重后取 Top-N
    - 词汇短语：计数后取高频词
    - 逻辑连接词：按类别汇总去重
    - 段落策略：抽取最典型描述

    返回:
        聚合后的风格特征字典
    """
    # 只取分析成功的 chunk
    valid = [c for c in analyzed_chunks if c.get("style_analysis")]
    if not valid:
        logger.warning("[Template] 没有有效的分析结果，无法生成模板")
        return {}

    logger.info(f"[Template] 聚合 {len(valid)} 个 chunk 的风格特征")

    # ── 按章节类型分组 ──
    by_section: Dict[str, List] = defaultdict(list)
    for chunk in valid:
        by_section[chunk.get("section_type", "body")].append(chunk["style_analysis"])

    aggregated = {
        "sentence_patterns": {},    # 按章节类型
        "vocabulary": {
            "academic_phrases": Counter(),
            "transition_words": Counter(),
        },
        "logic_connectors": {
            "contrast": Counter(),
            "addition": Counter(),
            "causation": Counter(),
            "concession": Counter(),
        },
        "hedging_language": Counter(),
        "citation_patterns": Counter(),
        "passive_voice": [],
        "style_summaries": [],
        "paragraph_strategies": defaultdict(list),
    }

    for section_type, analyses in by_section.items():
        all_patterns = []

        for analysis in analyses:
            # 句式模板
            for p in analysis.get("sentence_patterns", []):
                if isinstance(p, dict) and p.get("pattern"):
                    all_patterns.append(p)

            # 词汇
            vocab = analysis.get("vocabulary", {})
            for phrase in vocab.get("academic_phrases", []):
                aggregated["vocabulary"]["academic_phrases"][phrase] += 1
            for word in vocab.get("transition_words", []):
                aggregated["vocabulary"]["transition_words"][word] += 1

            # 逻辑连接词
            connectors = analysis.get("logic_connectors", {})
            for category in ["contrast", "addition", "causation", "concession"]:
                for word in connectors.get(category, []):
                    aggregated["logic_connectors"][category][word] += 1

            # Hedging
            for hedge in analysis.get("hedging_language", []):
                aggregated["hedging_language"][hedge] += 1

            # 引用
            cite = analysis.get("citation_style", {})
            for pattern in cite.get("embedding_patterns", []):
                aggregated["citation_patterns"][pattern] += 1

            # 被动语态
            pv = analysis.get("passive_voice_ratio", "")
            if pv:
                aggregated["passive_voice"].append(pv)

            # 风格摘要
            summary = analysis.get("style_summary", "")
            if summary:
                aggregated["style_summaries"].append(f"[{section_type}] {summary}")

            # 段落策略
            ps = analysis.get("paragraph_structure", {})
            if ps.get("opening_strategy"):
                aggregated["paragraph_strategies"][section_type].append(
                    ps["opening_strategy"]
                )

        # 去重句式：保留 common > occasional 的模板，最多 10 个/章节
        seen_patterns = set()
        unique_patterns = []
        priority = {"common": 0, "occasional": 1, "rare": 2}
        sorted_patterns = sorted(
            all_patterns,
            key=lambda x: priority.get(x.get("frequency", "rare"), 2)
        )
        for p in sorted_patterns:
            key = p["pattern"][:60]
            if key not in seen_patterns:
                seen_patterns.add(key)
                unique_patterns.append(p)
            if len(unique_patterns) >= 10:
                break

        aggregated["sentence_patterns"][section_type] = unique_patterns

    # 计算被动语态总体倾向
    pv_counts = Counter(aggregated["passive_voice"])
    aggregated["passive_voice_overall"] = pv_counts.most_common(1)[0][0] if pv_counts else "medium"

    return aggregated


# ──────────────────────────────────────────────
# 综合模板生成（Claude 二次归纳）
# ──────────────────────────────────────────────

def generate_training_prompts(aggregated: dict, paper_title: str = "") -> str:
    """
    调用 Claude 对聚合特征进行二次归纳，生成可训练自己写作的 Prompt 模板集

    参数:
        aggregated   - aggregate_features() 的返回值
        paper_title  - 论文标题（用于上下文）

    返回:
        格式化的 Prompt 模板集（Markdown 字符串）
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # 将聚合特征序列化为紧凑 JSON 传给 Claude
    features_json = json.dumps(
        {
            "sentence_patterns": aggregated.get("sentence_patterns", {}),
            "top_academic_phrases": dict(
                aggregated["vocabulary"]["academic_phrases"].most_common(20)
            ),
            "top_hedges": dict(aggregated["hedging_language"].most_common(10)),
            "style_summaries": aggregated.get("style_summaries", [])[:5],
            "passive_voice": aggregated.get("passive_voice_overall", "medium"),
        },
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""Based on the following writing style features extracted from a top academic paper 
("{paper_title}"), generate a comprehensive writing template guide.

<style_features>
{features_json}
</style_features>

Create a practical guide in Markdown format with:

1. **Introduction Section Template** — 3 sentence-level templates for introducing problems/motivation
2. **Methodology Section Template** — 3 templates for describing methods/models
3. **Results Section Template** — 3 templates for presenting findings
4. **Conclusion Section Template** — 2 templates for summarizing contributions

For each template:
- Provide the sentence pattern with [PLACEHOLDER] markers
- Explain when to use it
- Show a fill-in example

Then add:
5. **Style Rules** (5 bullet points): the most distinctive writing habits of this paper
6. **Training Prompt**: a single Claude prompt you could use to generate text in this exact style

Write clearly in both English explanations and Chinese annotations where helpful."""

    try:
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=config.CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"[Template] 生成训练 Prompt 失败: {e}")
        return ""


# ──────────────────────────────────────────────
# 输出函数
# ──────────────────────────────────────────────

def render_markdown_template(aggregated: dict,
                              training_prompts: str,
                              paper_title: str = "",
                              source_url: str = "") -> str:
    """
    将聚合特征渲染为人类可读的 Markdown 风格指南

    返回:
        完整的 Markdown 字符串
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 📝 论文写作风格模板",
        f"",
        f"- **来源论文**: {paper_title or '未知'}",
        f"- **原始链接**: {source_url or '未知'}",
        f"- **生成时间**: {now}",
        f"",
        "---",
        "",
        "## 一、句式模板库（按章节）",
        "",
    ]

    # 句式模板
    for section_type, patterns in aggregated.get("sentence_patterns", {}).items():
        if not patterns:
            continue
        lines.append(f"### {section_type.replace('_', ' ').title()}")
        lines.append("")
        for p in patterns:
            lines.append(f"**模板**: `{p.get('pattern', '')}`  ")
            lines.append(f"**用途**: {p.get('function', '')}  ")
            lines.append(f"**示例**: _{p.get('example', '')}_  ")
            lines.append(f"**频率**: {p.get('frequency', '')}  ")
            lines.append("")

    # 高频学术词组
    lines += [
        "---",
        "",
        "## 二、高频学术短语（Top 15）",
        "",
    ]
    top_phrases = aggregated["vocabulary"]["academic_phrases"].most_common(15)
    for phrase, count in top_phrases:
        lines.append(f"- `{phrase}` （出现 {count} 次）")
    lines.append("")

    # 逻辑连接词
    lines += [
        "---",
        "",
        "## 三、逻辑连接词",
        "",
    ]
    connector_labels = {
        "contrast": "对比/转折",
        "addition": "递进/补充",
        "causation": "因果",
        "concession": "让步",
    }
    for cat, label in connector_labels.items():
        top = aggregated["logic_connectors"][cat].most_common(6)
        if top:
            words = "、".join([f"`{w}`" for w, _ in top])
            lines.append(f"**{label}**: {words}  ")
    lines.append("")

    # Hedging
    lines += [
        "---",
        "",
        "## 四、学术限定语（Hedges）",
        "",
    ]
    for hedge, count in aggregated["hedging_language"].most_common(10):
        lines.append(f"- `{hedge}`")
    lines.append("")

    # 被动语态
    lines += [
        "---",
        "",
        "## 五、写作风格特征",
        "",
        f"- **被动语态使用程度**: {aggregated.get('passive_voice_overall', 'medium')}",
        "",
        "**风格描述摘要**:",
        "",
    ]
    for summary in aggregated.get("style_summaries", [])[:4]:
        lines.append(f"> {summary}")
        lines.append("")

    # 训练 Prompt
    if training_prompts:
        lines += [
            "---",
            "",
            "## 六、可复用训练模板与 Prompt",
            "",
            training_prompts,
            "",
        ]

    lines += [
        "---",
        "",
        "_本文件由 Paper Style Extractor 自动生成，仅供学习研究使用_",
        "",
    ]

    return "\n".join(lines)


def save_template(markdown: str, json_data: dict, paper_id: str) -> dict:
    """
    保存最终模板到文件系统

    返回:
        {"markdown_path": str, "json_path": str}
    """
    config.TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    md_path = config.TEMPLATE_DIR / f"style_template_{paper_id}.md"
    json_path = config.TEMPLATE_DIR / f"style_template_{paper_id}.json"

    md_path.write_text(markdown, encoding="utf-8")
    logger.info(f"[Template] Markdown 模板已保存: {md_path}")

    # JSON 中的 Counter 需要转换
    serializable = {}
    for k, v in json_data.items():
        if isinstance(v, Counter):
            serializable[k] = dict(v)
        elif isinstance(v, defaultdict):
            serializable[k] = {kk: list(vv) if not isinstance(vv, str) else vv
                               for kk, vv in v.items()}
        else:
            serializable[k] = v

    json_path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info(f"[Template] JSON 模板已保存: {json_path}")

    return {"markdown_path": str(md_path), "json_path": str(json_path)}
