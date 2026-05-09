# main.py — 主入口 + 验收出口
#
# 串联全部模块，提供 CLI 接口
# 用法：python main.py --url "https://arxiv.org/abs/2310.06825"
#       python main.py --url "..." --max-chunks 5 --sections introduction methodology
# ─────────────────────────────────────────────────────────────────

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# 项目根目录加入路径
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import config
from modules.scraper import fetch_html, fetch_pdf_bytes, resolve_arxiv_url
from modules.extractor import extract_from_html, extract_from_pdf
from modules.chunker import chunk_paper
from modules.analyzer import analyze_all_chunks
from modules.template_generator import (
    aggregate_features,
    generate_training_prompts,
    render_markdown_template,
    save_template,
)


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────

def setup_logging(paper_id: str = "run") -> logging.Logger:
    """配置日志：同时输出到控制台和文件"""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = config.LOG_DIR / f"{paper_id}_{timestamp}.log"

    handlers = [logging.StreamHandler(sys.stdout)]
    if config.LOG_TO_FILE:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=handlers,
        force=True,
    )
    return logging.getLogger("main")


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _make_paper_id(url: str) -> str:
    """从 URL 生成安全的文件名 ID"""
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_")
    # 截取末尾最多 40 字符，去除特殊字符
    safe = re.sub(r"[^\w\-.]", "_", path)[-40:]
    return safe or "paper"


def _save_intermediate(data, path: Path, label: str):
    """保存中间产物到 output 目录（验收用）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(str(data), encoding="utf-8")
    logging.getLogger("main").info(f"[Output] {label} → {path}")


# ──────────────────────────────────────────────
# 核心流水线
# ──────────────────────────────────────────────

def run_pipeline(
    url: str,
    output_dir: Path = None,
    max_chunks: int = 0,
    section_filter: list = None,
    force_pdf: bool = False,
) -> dict:
    """
    完整执行论文风格提取流水线

    参数:
        url           - 论文页面 URL
        output_dir    - 中间产物输出目录（默认 config.OUTPUT_DIR/<paper_id>/）
        max_chunks    - 最多分析的 chunk 数（0=全部；测试时建议设 3-5）
        section_filter - 只分析指定章节类型（None=全部）
        force_pdf     - 强制使用 PDF 模式（跳过 HTML 尝试）

    返回:
        {"status": "success"|"error", "outputs": {...}, "paper_id": str}
    """
    paper_id = _make_paper_id(url)
    logger = setup_logging(paper_id)
    output_dir = output_dir or (config.OUTPUT_DIR / paper_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info(f"  Paper Style Extractor — 开始处理")
    logger.info(f"  URL      : {url}")
    logger.info(f"  Paper ID : {paper_id}")
    logger.info(f"  输出目录 : {output_dir}")
    logger.info("=" * 60)

    outputs = {}
    start_time = time.time()

    # ────────────────────────────────────────
    # STEP 1: 爬取原始内容
    # ────────────────────────────────────────
    logger.info("\n【STEP 1】爬取论文页面")
    raw_html = None
    pdf_bytes = None

    # arXiv 特殊处理：优先 HTML 版本
    if "arxiv.org" in url and not force_pdf:
        arxiv_urls = resolve_arxiv_url(url)
        logger.info(f"  arXiv HTML: {arxiv_urls['html']}")
        raw_html = fetch_html(arxiv_urls["html"])
        if not raw_html:
            logger.warning("  arXiv HTML 获取失败，尝试 PDF")
            pdf_bytes = fetch_pdf_bytes(arxiv_urls["pdf"])
    elif url.lower().endswith(".pdf") or force_pdf:
        pdf_bytes = fetch_pdf_bytes(url)
    else:
        raw_html = fetch_html(url)

    if not raw_html and not pdf_bytes:
        logger.error("STEP 1 失败：无法获取任何内容，流程终止")
        return {"status": "error", "message": "爬取失败", "paper_id": paper_id}

    # 保存原始内容（验收出口1）
    if raw_html:
        raw_path = output_dir / "raw.html"
        _save_intermediate(raw_html, raw_path, "原始 HTML")
        outputs["raw_html"] = str(raw_path)
    if pdf_bytes:
        pdf_path = output_dir / "raw.pdf"
        pdf_path.write_bytes(pdf_bytes)
        logger.info(f"[Output] 原始 PDF → {pdf_path}")
        outputs["raw_pdf"] = str(pdf_path)

    logger.info("STEP 1 完成 ✓")

    # ────────────────────────────────────────
    # STEP 2: 提取论文结构
    # ────────────────────────────────────────
    logger.info("\n【STEP 2】提取论文结构（正文 + 章节）")
    if raw_html:
        paper = extract_from_html(raw_html, source_url=url)
    else:
        paper = extract_from_pdf(pdf_bytes)

    if not paper.get("sections"):
        logger.error("STEP 2 失败：未能提取到任何章节，请检查页面结构")
        return {"status": "error", "message": "提取失败", "paper_id": paper_id}

    logger.info(f"  标题     : {paper.get('title', '未知')[:80]}")
    logger.info(f"  摘要长度 : {len(paper.get('abstract', ''))} 字符")
    logger.info(f"  章节数   : {len(paper['sections'])}")
    for sec in paper["sections"]:
        logger.info(f"    - [{sec['section_type']}] {sec['title'][:60]}")

    # 保存结构化正文（验收出口2）
    extracted_path = output_dir / "extracted.json"
    _save_intermediate(paper, extracted_path, "结构化正文")
    outputs["extracted"] = str(extracted_path)
    logger.info("STEP 2 完成 ✓")

    # ────────────────────────────────────────
    # STEP 3: 文本分块
    # ────────────────────────────────────────
    logger.info("\n【STEP 3】文本分块（Chunking）")
    chunks = chunk_paper(paper)

    if not chunks:
        logger.error("STEP 3 失败：分块结果为空")
        return {"status": "error", "message": "分块失败", "paper_id": paper_id}

    total_tokens = sum(c["tokens"] for c in chunks)
    logger.info(f"  共 {len(chunks)} 个 chunk，合计约 {total_tokens} tokens")

    # 保存分块结果（验收出口3）
    chunks_path = output_dir / "chunks.json"
    _save_intermediate(chunks, chunks_path, "分块结果")
    outputs["chunks"] = str(chunks_path)
    logger.info("STEP 3 完成 ✓")

    # ────────────────────────────────────────
    # STEP 4: Claude 语义分析
    # ────────────────────────────────────────
    logger.info("\n【STEP 4】Claude 语义分析")
    if not config.ANTHROPIC_API_KEY:
        logger.error("未设置 ANTHROPIC_API_KEY，跳过分析步骤")
        return {"status": "error", "message": "API Key 未配置", "paper_id": paper_id}

    analyzed = analyze_all_chunks(
        chunks,
        max_chunks=max_chunks,
        section_filter=section_filter,
    )

    # 保存分析结果（验收出口4）
    analysis_path = output_dir / "analysis.json"
    _save_intermediate(analyzed, analysis_path, "语义分析结果")
    outputs["analysis"] = str(analysis_path)
    logger.info("STEP 4 完成 ✓")

    # ────────────────────────────────────────
    # STEP 5: 生成风格模板
    # ────────────────────────────────────────
    logger.info("\n【STEP 5】生成风格模板")
    aggregated = aggregate_features(analyzed)

    if not aggregated:
        logger.error("STEP 5 失败：特征聚合为空，无法生成模板")
        return {"status": "error", "message": "模板生成失败", "paper_id": paper_id}

    paper_title = paper.get("title", "")
    training_prompts = generate_training_prompts(aggregated, paper_title)
    markdown = render_markdown_template(
        aggregated, training_prompts, paper_title, url
    )

    saved = save_template(markdown, aggregated, paper_id)
    outputs["template_md"] = saved["markdown_path"]
    outputs["template_json"] = saved["json_path"]
    logger.info("STEP 5 完成 ✓")

    # ────────────────────────────────────────
    # 验收摘要
    # ────────────────────────────────────────
    elapsed = time.time() - start_time
    logger.info("\n" + "=" * 60)
    logger.info("  ✅ 全流程完成！")
    logger.info(f"  耗时: {elapsed:.1f}s")
    logger.info("")
    logger.info("  📦 交付物清单（验收出口）:")
    for label, path in outputs.items():
        logger.info(f"    [{label}] {path}")
    logger.info("")
    logger.info(f"  🎯 核心交付物:")
    logger.info(f"    {outputs.get('template_md', 'N/A')}")
    logger.info("=" * 60)

    return {
        "status": "success",
        "paper_id": paper_id,
        "paper_title": paper_title,
        "outputs": outputs,
        "stats": {
            "sections": len(paper["sections"]),
            "chunks": len(chunks),
            "analyzed_chunks": len([c for c in analyzed if c.get("style_analysis")]),
            "elapsed_seconds": round(elapsed, 1),
        }
    }


# ──────────────────────────────────────────────
# CLI 接口
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Paper Style Extractor — 学术论文写作风格提取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --url "https://arxiv.org/abs/2310.06825"
  python main.py --url "https://arxiv.org/abs/2310.06825" --max-chunks 5
  python main.py --url "https://arxiv.org/abs/2310.06825" \\
                 --sections introduction methodology conclusion
        """,
    )

    parser.add_argument(
        "--url", required=True,
        help="目标论文 URL（支持 arXiv / ACL / 通用页面）"
    )
    parser.add_argument(
        "--output", default=None,
        help="中间产物输出目录（默认 output/<paper_id>/）"
    )
    parser.add_argument(
        "--max-chunks", type=int, default=0,
        help="最多分析的 chunk 数（0=全部；测试时建议 3-5）"
    )
    parser.add_argument(
        "--sections", nargs="+", default=None,
        metavar="SECTION_TYPE",
        help="只分析指定章节类型，例如: introduction methodology conclusion"
    )
    parser.add_argument(
        "--pdf", action="store_true",
        help="强制使用 PDF 模式"
    )
    parser.add_argument(
        "--api-key",
        help="Claude API Key（覆盖环境变量 ANTHROPIC_API_KEY）"
    )

    args = parser.parse_args()

    # CLI 传入的 API Key 优先
    if args.api_key:
        config.ANTHROPIC_API_KEY = args.api_key

    output_dir = Path(args.output) if args.output else None

    result = run_pipeline(
        url=args.url,
        output_dir=output_dir,
        max_chunks=args.max_chunks,
        section_filter=args.sections,
        force_pdf=args.pdf,
    )

    # 以退出码反映结果（0=成功，1=失败）
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
