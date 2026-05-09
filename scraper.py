# modules/scraper.py — 模块1：网页爬虫 + 反爬策略
#
# 职责：接收目标论文 URL，获取 HTML 或 PDF 原始内容
# 反爬手段：UA 轮换、随机延迟、重试退避、代理支持、Selenium fallback
# ─────────────────────────────────────────────────────────────────

import random
import time
import logging
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 导入全局配置
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 辅助工具
# ──────────────────────────────────────────────

def _random_delay():
    """在 [MIN, MAX] 区间内随机休眠，模拟人工浏览间隔"""
    delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
    logger.debug(f"[Scraper] 随机延迟 {delay:.2f}s")
    time.sleep(delay)


def _random_headers() -> dict:
    """随机选取 User-Agent，拼装请求头，降低被识别为爬虫的概率"""
    ua = random.choice(config.USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def _build_session() -> requests.Session:
    """
    构建带有重试策略的 requests.Session
    - 3xx 重定向自动跟随
    - 5xx 服务端错误自动重试（指数退避）
    """
    session = requests.Session()

    retry_strategy = Retry(
        total=config.MAX_RETRIES,
        backoff_factor=config.RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    # 代理配置（若启用）
    if config.USE_PROXY and config.PROXY_LIST:
        proxy = random.choice(config.PROXY_LIST)
        session.proxies = {"http": proxy, "https": proxy}
        logger.info(f"[Scraper] 使用代理: {proxy}")

    return session


def _check_robots_txt(url: str) -> bool:
    """
    检查目标网站 robots.txt，判断路径是否允许爬取
    返回 True 表示允许，False 表示被禁止
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        allowed = rp.can_fetch("*", url)
        if not allowed:
            logger.warning(f"[Scraper] robots.txt 禁止访问: {url}")
        return allowed
    except Exception as e:
        # robots.txt 读取失败时，保守地允许继续（记录警告）
        logger.warning(f"[Scraper] 无法读取 robots.txt ({robots_url}): {e}")
        return True


# ──────────────────────────────────────────────
# 核心爬取函数
# ──────────────────────────────────────────────

def fetch_html(url: str, respect_robots: bool = True) -> str | None:
    """
    爬取目标 URL 的 HTML 内容

    参数:
        url             - 目标页面完整 URL
        respect_robots  - 是否遵守 robots.txt（建议 True）

    返回:
        str: HTML 文本内容
        None: 爬取失败
    """
    logger.info(f"[Scraper] 开始爬取 HTML: {url}")

    # Step 1: robots.txt 合规检查
    if respect_robots and not _check_robots_txt(url):
        logger.error("[Scraper] 已被 robots.txt 禁止，终止爬取")
        return None

    session = _build_session()

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            _random_delay()
            headers = _random_headers()
            response = session.get(
                url,
                headers=headers,
                timeout=config.REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            if response.status_code == 200:
                logger.info(f"[Scraper] HTML 爬取成功，大小: {len(response.text)} 字符")
                return response.text

            elif response.status_code == 403:
                logger.warning(f"[Scraper] 403 Forbidden（尝试 {attempt}/{config.MAX_RETRIES}），"
                               "考虑启用 USE_SELENIUM 或代理")
                if config.USE_SELENIUM:
                    return _fetch_with_selenium(url)
                break

            elif response.status_code == 429:
                wait = config.RETRY_BACKOFF ** attempt * 5
                logger.warning(f"[Scraper] 429 限速，等待 {wait:.0f}s 后重试")
                time.sleep(wait)

            else:
                logger.warning(f"[Scraper] HTTP {response.status_code}（尝试 {attempt}/{config.MAX_RETRIES}）")

        except requests.exceptions.ConnectionError as e:
            logger.error(f"[Scraper] 连接错误（尝试 {attempt}）: {e}")
        except requests.exceptions.Timeout:
            logger.error(f"[Scraper] 请求超时（尝试 {attempt}），超时设置: {config.REQUEST_TIMEOUT}s")
        except Exception as e:
            logger.exception(f"[Scraper] 未知异常（尝试 {attempt}）: {e}")

        time.sleep(config.RETRY_BACKOFF ** attempt)

    logger.error(f"[Scraper] 全部重试失败: {url}")
    return None


def fetch_pdf_bytes(url: str) -> bytes | None:
    """
    下载 PDF 文件的原始字节流

    参数:
        url - PDF 直链（通常以 .pdf 结尾）

    返回:
        bytes: PDF 文件字节内容
        None: 下载失败
    """
    logger.info(f"[Scraper] 下载 PDF: {url}")
    session = _build_session()

    try:
        _random_delay()
        response = session.get(
            url,
            headers=_random_headers(),
            timeout=config.REQUEST_TIMEOUT * 2,  # PDF 文件较大，超时时间翻倍
            stream=True,
        )
        if response.status_code == 200:
            content = response.content
            logger.info(f"[Scraper] PDF 下载成功，大小: {len(content) / 1024:.1f} KB")
            return content
        else:
            logger.error(f"[Scraper] PDF 下载失败，HTTP {response.status_code}")
            return None
    except Exception as e:
        logger.exception(f"[Scraper] PDF 下载异常: {e}")
        return None


def _fetch_with_selenium(url: str) -> str | None:
    """
    使用 Selenium 处理 JS 渲染的页面（备用方案）
    需要安装: pip install selenium webdriver-manager
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        logger.info(f"[Scraper] 切换 Selenium 模式: {url}")

        options = Options()
        if config.SELENIUM_HEADLESS:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-agent={random.choice(config.USER_AGENTS)}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )

        try:
            driver.get(url)
            time.sleep(config.SELENIUM_WAIT_SECONDS)  # 等待 JS 渲染
            html = driver.page_source
            logger.info(f"[Scraper] Selenium 获取成功，大小: {len(html)} 字符")
            return html
        finally:
            driver.quit()

    except ImportError:
        logger.error("[Scraper] Selenium 未安装，请执行: pip install selenium webdriver-manager")
        return None
    except Exception as e:
        logger.exception(f"[Scraper] Selenium 异常: {e}")
        return None


# ──────────────────────────────────────────────
# arXiv 专用辅助（自动识别 HTML/PDF 链接）
# ──────────────────────────────────────────────

def resolve_arxiv_url(url: str) -> dict:
    """
    解析 arXiv URL，返回 HTML 版本与 PDF 直链
    arXiv 提供 HTML 版本（更易解析）优先使用

    输入示例:
        https://arxiv.org/abs/2310.06825
        https://arxiv.org/pdf/2310.06825

    返回:
        {"html": "https://arxiv.org/html/...", "pdf": "https://arxiv.org/pdf/..."}
    """
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")

    # 提取论文 ID（如 2310.06825 或 2310.06825v2）
    paper_id = None
    for part in path_parts:
        if part not in ("abs", "pdf", "html") and ("." in part or part.isdigit()):
            paper_id = part.replace(".pdf", "")
            break

    if not paper_id:
        logger.warning(f"[Scraper] 无法从 URL 解析 arXiv ID: {url}")
        return {"html": url, "pdf": url}

    return {
        "html": f"https://arxiv.org/html/{paper_id}",
        "pdf": f"https://arxiv.org/pdf/{paper_id}",
        "abs": f"https://arxiv.org/abs/{paper_id}",
        "paper_id": paper_id,
    }
