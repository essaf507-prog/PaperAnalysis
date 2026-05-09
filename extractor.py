# modules/extractor.py — 模块2：正文提取 + 论文结构识别
#
# 职责：从 HTML 或 PDF 字节中提取结构化论文内容
# 输出：{"title", "abstract", "sections": [{"title", "content"}]}
# ─────────────────────────────────────────────────────────────────

import re
import io
import logging
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# HTML 提取
# ──────────────────────────────────────────────

def extract_from_html(html: str, source_url: str = "") -> dict:
    """
    从 HTML 字符串提取论文结构化内容

    优先尝试以下策略（按顺序）：
    1. arXiv HTML 格式（section[data-section] 标签）
    2. 通用学术页面（h1/h2/h3 + p 段落）
    3. 降级：提取所有 <p> 段落文本

    返回:
        {
          "title": str,
          "abstract": str,
          "sections": [{"title": str, "content": str}],
          "source_url": str
        }
    """
    logger.info("[Extractor] 开始解析 HTML 内容")
    soup = BeautifulSoup(html, "html.parser")

    # ─── 清除噪声元素（导航、广告、脚注、数学公式符号）───
    for tag in soup.find_all(["nav", "header", "footer", "script",
                               "style", "noscript", "aside"]):
        tag.decompose()
    # 移除通常是引用注释的 sup 标签
    for sup in soup.find_all("sup"):
        sup.decompose()

    result = {
        "title": _extract_title(soup),
        "abstract": "",
        "sections": [],
        "source_url": source_url,
    }

    # ─── 尝试 arXiv HTML 格式 ───
    if _is_arxiv_html(soup):
        logger.info("[Extractor] 检测到 arXiv HTML 格式")
        result.update(_parse_arxiv_html(soup))
        return result

    # ─── 通用格式：按标题标签切分 ───
    result.update(_parse_generic_html(soup))
    logger.info(f"[Extractor] 提取完成，共 {len(result['sections'])} 个章节")
    return result


def _is_arxiv_html(soup: BeautifulSoup) -> bool:
    """判断是否为 arXiv 的 HTML 格式页面"""
    return bool(
        soup.find("article", class_=re.compile(r"ltx_document")) or
        soup.find("section", {"data-heading-level": True}) or
        soup.find("div", class_="ltx_abstract")
    )


def _parse_arxiv_html(soup: BeautifulSoup) -> dict:
    """解析 arXiv HTML 格式（LaTeXML 渲染）"""
    sections = []

    # 提取摘要
    abstract = ""
    abstract_div = soup.find("div", class_="ltx_abstract")
    if abstract_div:
        abstract = _clean_text(abstract_div.get_text())

    # 提取各 section
    for sec in soup.find_all("section", class_=re.compile(r"ltx_section")):
        title_tag = sec.find(["h2", "h3", "h4"])
        section_title = _clean_text(title_tag.get_text()) if title_tag else "Untitled"

        # 跳过引用列表（通常不做风格分析）
        if _match_section_type(section_title) == "references":
            continue

        paragraphs = [_clean_text(p.get_text()) for p in sec.find_all("p")
                      if len(p.get_text().strip()) > 50]
        content = "\n\n".join(paragraphs)

        if content:
            sections.append({
                "title": section_title,
                "section_type": _match_section_type(section_title),
                "content": content,
            })

    return {"abstract": abstract, "sections": sections}


def _parse_generic_html(soup: BeautifulSoup) -> dict:
    """通用 HTML 格式：按 h1/h2/h3 标题切分段落"""
    sections = []
    abstract = ""

    # 寻找摘要（class 或 id 含 abstract）
    abstract_candidates = soup.find_all(
        attrs={"class": re.compile(r"abstract", re.I)}
    ) + soup.find_all(attrs={"id": re.compile(r"abstract", re.I)})
    if abstract_candidates:
        abstract = _clean_text(abstract_candidates[0].get_text())

    # 按标题标签切分
    heading_tags = soup.find_all(["h1", "h2", "h3"])
    for i, heading in enumerate(heading_tags):
        section_title = _clean_text(heading.get_text())
        if not section_title or len(section_title) > 120:
            continue

        # 收集该标题到下一个标题之间的所有 <p> 段落
        paragraphs = []
        sibling = heading.find_next_sibling()
        while sibling:
            if sibling.name in ["h1", "h2", "h3"]:
                break
            if sibling.name == "p":
                text = _clean_text(sibling.get_text())
                if len(text) > 40:
                    paragraphs.append(text)
            sibling = sibling.find_next_sibling()

        content = "\n\n".join(paragraphs)
        section_type = _match_section_type(section_title)

        if section_type == "references":
            continue

        if content:
            sections.append({
                "title": section_title,
                "section_type": section_type,
                "content": content,
            })

    return {"abstract": abstract, "sections": sections}


# ──────────────────────────────────────────────
# PDF 提取
# ──────────────────────────────────────────────

def extract_from_pdf(pdf_bytes: bytes) -> dict:
    """
    从 PDF 字节流中提取文本并识别论文结构

    使用 pdfplumber 进行文本层提取（仅适用于有文字层的 PDF）
    扫描件 PDF 需要 OCR，此版本不支持

    返回格式与 extract_from_html 相同
    """
    try:
        import pdfplumber
    except ImportError:
        logger.error("[Extractor] 请安装 pdfplumber: pip install pdfplumber")
        return _empty_result()

    logger.info("[Extractor] 开始解析 PDF 内容")
    full_text_pages = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        logger.info(f"[Extractor] PDF 共 {len(pdf.pages)} 页")
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3)
            if text:
                full_text_pages.append(text)

    full_text = "\n".join(full_text_pages)
    return _parse_text_by_headings(full_text)


def _parse_text_by_headings(text: str) -> dict:
    """
    对纯文本按行启发式识别标题，切分段落
    标题特征：
      - 行较短（< 80 字符）
      - 含章节编号（如 "1 Introduction", "2.1 Method"）
      - 全大写或首字母大写
    """
    lines = text.split("\n")
    sections = []
    abstract = ""
    current_title = "Preamble"
    current_paragraphs = []

    heading_pattern = re.compile(
        r"^(\d+\.?\d*\.?\s+[A-Z][A-Za-z\s]+|[A-Z][A-Z\s]{3,50})$"
    )

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 检测标题行
        if heading_pattern.match(line) and len(line) < 80:
            # 保存上一个 section
            if current_paragraphs:
                content = "\n\n".join(current_paragraphs)
                section_type = _match_section_type(current_title)

                if section_type == "abstract":
                    abstract = content
                elif section_type != "references" and content:
                    sections.append({
                        "title": current_title,
                        "section_type": section_type,
                        "content": content,
                    })

            current_title = _clean_text(line)
            current_paragraphs = []
        else:
            # 非标题行：累积段落文本
            if current_paragraphs and len(current_paragraphs[-1]) < 200:
                current_paragraphs[-1] += " " + line
            else:
                current_paragraphs.append(line)

    # 保存最后一个 section
    if current_paragraphs and current_title:
        content = "\n\n".join(current_paragraphs)
        if _match_section_type(current_title) != "references":
            sections.append({
                "title": current_title,
                "section_type": _match_section_type(current_title),
                "content": content,
            })

    title = sections[0]["content"][:80] if sections else ""
    logger.info(f"[Extractor] PDF 提取完成，共 {len(sections)} 个章节")
    return {"title": title, "abstract": abstract, "sections": sections, "source_url": ""}


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _extract_title(soup: BeautifulSoup) -> str:
    """尝试多种策略提取论文标题"""
    # 方案1：<title> 标签
    if soup.title:
        return _clean_text(soup.title.get_text())

    # 方案2：最大的 h1
    h1 = soup.find("h1")
    if h1:
        return _clean_text(h1.get_text())

    # 方案3：class 含 title 的元素
    title_el = soup.find(attrs={"class": re.compile(r"\btitle\b", re.I)})
    if title_el:
        return _clean_text(title_el.get_text())

    return "Unknown Title"


def _match_section_type(title: str) -> str:
    """将章节标题映射到预定义的论文结构类型"""
    title_lower = title.lower()
    for section_type, keywords in config.SECTION_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            return section_type
    return "body"


def _clean_text(text: str) -> str:
    """清洗文本：去除多余空白、控制字符、BOM"""
    text = text.replace("\xa0", " ")          # 非断行空格
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)  # 控制字符
    text = re.sub(r" {2,}", " ", text)        # 多余空格
    text = re.sub(r"\n{3,}", "\n\n", text)   # 多余换行
    return text.strip()


def _empty_result() -> dict:
    return {"title": "", "abstract": "", "sections": [], "source_url": ""}
