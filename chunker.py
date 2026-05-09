# modules/chunker.py — 模块3：文本分块（Chunking）
#
# 职责：将论文章节内容切分为适合 LLM 处理的语义块
# 策略：按段落边界切分 + 滑动窗口重叠，保留 section 标签
# ─────────────────────────────────────────────────────────────────

import re
import logging
from pathlib import Path
from typing import List

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Token 估算（轻量级，无需加载 tokenizer）
# ──────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """
    粗估 token 数（英文约 1 token = 4 字符）
    足够用于分块控制，无需精确
    """
    return max(1, len(text) // 4)


# ──────────────────────────────────────────────
# 段落切分工具
# ──────────────────────────────────────────────

def _split_into_paragraphs(text: str) -> List[str]:
    """
    将文本按段落切分
    段落边界：连续两个换行符，或明显的句子结尾+换行
    """
    # 按双换行切分
    paragraphs = re.split(r"\n\s*\n", text)

    result = []
    for para in paragraphs:
        para = para.strip()
        if len(para) < 20:   # 忽略过短片段（如孤立的章节编号）
            continue
        result.append(para)

    return result


def _split_long_paragraph(para: str, max_tokens: int) -> List[str]:
    """
    对超过 max_tokens 的单段落，按句子边界进一步切分
    避免在句子中间截断
    """
    # 按句子边界切分（. / ! / ? 后跟空格）
    sentences = re.split(r"(?<=[.!?])\s+", para)

    chunks = []
    current = ""

    for sent in sentences:
        candidate = (current + " " + sent).strip()
        if _estimate_tokens(candidate) <= max_tokens:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # 如果单句本身超长，强制按字符截断（保底）
            if _estimate_tokens(sent) > max_tokens:
                char_limit = max_tokens * 4
                while sent:
                    chunks.append(sent[:char_limit])
                    sent = sent[char_limit:]
            else:
                current = sent

    if current:
        chunks.append(current)

    return chunks


# ──────────────────────────────────────────────
# 主分块函数
# ──────────────────────────────────────────────

def chunk_section(section: dict,
                  chunk_size: int = None,
                  chunk_overlap: int = None) -> List[dict]:
    """
    对单个论文章节进行分块

    参数:
        section      - {"title": str, "section_type": str, "content": str}
        chunk_size   - 最大 token 数（默认使用 config.CHUNK_SIZE）
        chunk_overlap - 重叠 token 数（默认使用 config.CHUNK_OVERLAP）

    返回:
        [
          {
            "chunk_id": int,        # 全局递增 ID（由调用方传入 offset 处理）
            "section_title": str,   # 所属章节标题
            "section_type": str,    # 章节类型（abstract/introduction/...）
            "text": str,            # chunk 正文
            "tokens": int,          # 估算 token 数
          }
        ]
    """
    chunk_size = chunk_size or config.CHUNK_SIZE
    chunk_overlap = chunk_overlap or config.CHUNK_OVERLAP

    section_title = section.get("title", "Unknown")
    section_type = section.get("section_type", "body")
    content = section.get("content", "")

    if not content.strip():
        return []

    paragraphs = _split_into_paragraphs(content)

    raw_chunks = []
    current_chunk = ""

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        # 段落本身超过单块上限 → 先拆句
        if para_tokens > chunk_size:
            sub_chunks = _split_long_paragraph(para, chunk_size)
            for sub in sub_chunks:
                if _estimate_tokens((current_chunk + "\n\n" + sub).strip()) <= chunk_size:
                    current_chunk = (current_chunk + "\n\n" + sub).strip()
                else:
                    if current_chunk:
                        raw_chunks.append(current_chunk)
                    current_chunk = sub
        else:
            candidate = (current_chunk + "\n\n" + para).strip()
            if _estimate_tokens(candidate) <= chunk_size:
                current_chunk = candidate
            else:
                if current_chunk:
                    raw_chunks.append(current_chunk)
                current_chunk = para

    if current_chunk:
        raw_chunks.append(current_chunk)

    # ── 添加滑动重叠（将上一块的最后 chunk_overlap tokens 追加到当前块开头）──
    final_chunks = []
    overlap_chars = chunk_overlap * 4  # token → 字符估算

    for i, text in enumerate(raw_chunks):
        if i > 0 and chunk_overlap > 0:
            # 取上一块末尾作为重叠前缀
            prev_tail = raw_chunks[i - 1][-overlap_chars:]
            # 在句子边界截取（不要从句子中间开始）
            match = re.search(r"(?<=[.!?])\s+", prev_tail)
            if match:
                prev_tail = prev_tail[match.end():]
            text = prev_tail.strip() + " " + text

        token_count = _estimate_tokens(text)

        # 过短 chunk 与下一块合并（由上层逻辑处理，此处仅记录）
        if token_count < config.MIN_CHUNK_TOKENS and i < len(raw_chunks) - 1:
            raw_chunks[i + 1] = text + "\n\n" + raw_chunks[i + 1]
            continue

        final_chunks.append({
            "chunk_id": -1,          # 由 chunk_paper() 统一编号
            "section_title": section_title,
            "section_type": section_type,
            "text": text.strip(),
            "tokens": token_count,
        })

    logger.debug(f"[Chunker] 章节 '{section_title}' → {len(final_chunks)} 个 chunk")
    return final_chunks


def chunk_paper(paper: dict) -> List[dict]:
    """
    对整篇论文进行分块，整合摘要与所有章节

    参数:
        paper - extract_from_html / extract_from_pdf 的返回值

    返回:
        全局编号的 chunk 列表
    """
    all_chunks = []
    chunk_id = 1

    # 先处理摘要
    abstract = paper.get("abstract", "")
    if abstract and len(abstract.strip()) > 50:
        abstract_section = {
            "title": "Abstract",
            "section_type": "abstract",
            "content": abstract,
        }
        for chunk in chunk_section(abstract_section):
            chunk["chunk_id"] = chunk_id
            all_chunks.append(chunk)
            chunk_id += 1

    # 处理各章节
    for section in paper.get("sections", []):
        for chunk in chunk_section(section):
            chunk["chunk_id"] = chunk_id
            all_chunks.append(chunk)
            chunk_id += 1

    total_tokens = sum(c["tokens"] for c in all_chunks)
    logger.info(f"[Chunker] 全文分块完成: {len(all_chunks)} 个 chunk，"
                f"共约 {total_tokens} tokens（估算）")
    return all_chunks
