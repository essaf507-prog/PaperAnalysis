# modules/analyzer.py — 模块4：Claude 语义分析接口
#
# 职责：调用 Claude API 对每个 chunk 进行多维度写作风格分析
# 输出：结构化 JSON，包含句式、词汇、逻辑、段落组织等特征
# ─────────────────────────────────────────────────────────────────

import json
import time
import logging
from pathlib import Path
from typing import List, Optional

import anthropic

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

# 初始化 Anthropic 客户端（全局单例）
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    """惰性初始化 Anthropic 客户端，避免启动时校验 key"""
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise ValueError(
                "未设置 ANTHROPIC_API_KEY，请在环境变量或 config.py 中配置"
            )
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ──────────────────────────────────────────────
# Prompt 模板
# ──────────────────────────────────────────────

ANALYSIS_SYSTEM_PROMPT = """You are an expert in academic writing style analysis, 
specializing in NLP and computational linguistics. 
Your task is to analyze a passage from a top-venue academic paper and extract 
reusable writing style features.

Always respond with valid JSON only. No explanation outside the JSON object."""


def _build_analysis_prompt(chunk_text: str, section_type: str) -> str:
    """
    构建发送给 Claude 的分析提示词

    分析维度：
    1. sentence_patterns  - 高频句式模板（含占位符）
    2. vocabulary         - 学术词汇与短语习惯
    3. logic_connectors   - 逻辑连接词使用模式
    4. paragraph_structure- 段落组织方式
    5. hedging_language   - 学术模糊限定语（hedges）
    6. citation_style     - 引用嵌入方式（如有）
    7. style_summary      - 一段简短的风格总结
    """
    return f"""Analyze the following academic paper excerpt from the "{section_type}" section.
Extract reusable writing style patterns that can be used as a template for academic writing.

<excerpt>
{chunk_text}
</excerpt>

Return a JSON object with this exact structure:
{{
  "sentence_patterns": [
    {{
      "pattern": "sentence template with [PLACEHOLDER] for key terms",
      "example": "original sentence from the text",
      "frequency": "common/occasional/rare",
      "function": "what rhetorical purpose this pattern serves"
    }}
  ],
  "vocabulary": {{
    "academic_phrases": ["list of 3-6 word academic collocations found"],
    "transition_words": ["logical connectors used"],
    "domain_terms_style": "how domain-specific terms are introduced and used"
  }},
  "logic_connectors": {{
    "contrast": ["adversative words/phrases used (e.g. however, while, although)"],
    "addition": ["additive connectors (e.g. furthermore, in addition)"],
    "causation": ["causal connectors (e.g. therefore, thus, consequently)"],
    "concession": ["concessive phrases used"]
  }},
  "paragraph_structure": {{
    "opening_strategy": "how paragraphs typically begin (topic sentence style)",
    "closing_strategy": "how paragraphs typically end",
    "avg_sentences_per_paragraph": "estimated number"
  }},
  "hedging_language": [
    "list of hedging expressions found (e.g. 'we argue that', 'this suggests', 'it appears')"
  ],
  "citation_style": {{
    "embedding_patterns": ["how citations are woven into sentences"],
    "attribution_phrases": ["phrases used to attribute claims"]
  }},
  "passive_voice_ratio": "low/medium/high — estimated proportion of passive constructions",
  "style_summary": "2-3 sentences summarizing the distinctive writing style of this section"
}}"""


# ──────────────────────────────────────────────
# 核心分析函数
# ──────────────────────────────────────────────

def analyze_chunk(chunk: dict, retry: int = 2) -> dict:
    """
    对单个 chunk 调用 Claude API 进行风格分析

    参数:
        chunk  - chunker.py 输出的单个 chunk dict
        retry  - API 调用失败时的重试次数

    返回:
        在原 chunk 基础上追加 "style_analysis" 字段
    """
    chunk_id = chunk.get("chunk_id", "?")
    section_type = chunk.get("section_type", "body")
    text = chunk.get("text", "")

    logger.info(f"[Analyzer] 分析 chunk #{chunk_id}（{section_type}，"
                f"约 {chunk.get('tokens', 0)} tokens）")

    prompt = _build_analysis_prompt(text, section_type)
    client = _get_client()

    for attempt in range(1, retry + 2):
        try:
            response = client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.CLAUDE_MAX_TOKENS,
                temperature=config.CLAUDE_TEMPERATURE,
                system=ANALYSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            raw_text = response.content[0].text.strip()

            # 解析返回的 JSON（防御性处理）
            # 去除可能的 markdown 代码块包裹
            if raw_text.startswith("```"):
                raw_text = re.sub(r"```(?:json)?", "", raw_text).strip("` \n")

            analysis = json.loads(raw_text)
            result = {**chunk, "style_analysis": analysis}
            logger.debug(f"[Analyzer] chunk #{chunk_id} 分析完成")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[Analyzer] chunk #{chunk_id} JSON 解析失败（尝试 {attempt}）: {e}")
            if attempt <= retry:
                time.sleep(2 ** attempt)
        except anthropic.RateLimitError:
            wait = 2 ** attempt * 5
            logger.warning(f"[Analyzer] Rate limit，等待 {wait}s 后重试")
            time.sleep(wait)
        except anthropic.APIError as e:
            logger.error(f"[Analyzer] API 错误（尝试 {attempt}）: {e}")
            if attempt <= retry:
                time.sleep(2 ** attempt)
        except Exception as e:
            logger.exception(f"[Analyzer] 未知异常（尝试 {attempt}）: {e}")

    # 分析失败：保留原 chunk，style_analysis 置空
    logger.error(f"[Analyzer] chunk #{chunk_id} 分析失败，已跳过")
    return {**chunk, "style_analysis": None}


def analyze_all_chunks(chunks: List[dict],
                       max_chunks: int = 0,
                       section_filter: List[str] = None) -> List[dict]:
    """
    批量分析所有 chunk

    参数:
        chunks         - 完整 chunk 列表
        max_chunks     - 最大分析数量（0=全部，用于测试时限制费用）
        section_filter - 只分析特定类型章节（如 ["introduction", "methodology"]）
                         None 表示分析全部

    返回:
        带有 style_analysis 字段的 chunk 列表
    """
    # 过滤
    target_chunks = chunks
    if section_filter:
        target_chunks = [c for c in chunks if c.get("section_type") in section_filter]
        logger.info(f"[Analyzer] 过滤后剩余 {len(target_chunks)} 个 chunk "
                    f"（section_filter={section_filter}）")

    if max_chunks > 0:
        target_chunks = target_chunks[:max_chunks]
        logger.info(f"[Analyzer] 限制最多分析 {max_chunks} 个 chunk")

    results = []
    total = len(target_chunks)

    for i, chunk in enumerate(target_chunks, 1):
        logger.info(f"[Analyzer] 进度 {i}/{total}")
        analyzed = analyze_chunk(chunk)
        results.append(analyzed)

        # API 调用间适当休眠，避免触发限速
        if i < total:
            time.sleep(0.8)

    success_count = sum(1 for r in results if r.get("style_analysis") is not None)
    logger.info(f"[Analyzer] 批量分析完成: {success_count}/{total} 成功")
    return results


# ──────────────────────────────────────────────
# 需要在文件顶部补充的 import
# ──────────────────────────────────────────────
import re   # noqa: E402  （移至文件顶部，此处保留避免破坏可读性）
