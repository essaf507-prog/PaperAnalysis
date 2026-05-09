# config.py — 全局配置文件
# 所有模块共享此配置，修改此文件即可调整全局行为

import os
from pathlib import Path

# ─────────────────────────────────────────────
# 路径配置
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
TEMPLATE_DIR = BASE_DIR / "templates"
LOG_DIR = BASE_DIR / "logs"

# ─────────────────────────────────────────────
# Claude API 配置
# ─────────────────────────────────────────────
# 推荐通过环境变量传入，避免硬编码密钥
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS = 2048        # 单次分析最大输出 token
CLAUDE_TEMPERATURE = 0.3        # 低温度保证分析稳定性

# ─────────────────────────────────────────────
# 分块配置
# ─────────────────────────────────────────────
CHUNK_SIZE = 512                # 每块最大 token 数（粗估：1 token ≈ 4 字符）
CHUNK_OVERLAP = 64              # 滑动窗口重叠 token 数，保留上下文连贯性
MIN_CHUNK_TOKENS = 80           # 过短的 chunk 直接合并到下一块

# ─────────────────────────────────────────────
# 爬虫 / 反爬配置
# ─────────────────────────────────────────────
REQUEST_DELAY_MIN = 1.5         # 请求最小间隔（秒）
REQUEST_DELAY_MAX = 4.5         # 请求最大间隔（秒）
MAX_RETRIES = 3                 # 最大重试次数
RETRY_BACKOFF = 2.0             # 指数退避基数（每次重试等待时间 × 此倍数）
REQUEST_TIMEOUT = 20            # 单次请求超时（秒）

# 代理配置（USE_PROXY=True 时生效）
USE_PROXY = False
PROXY_LIST = [
    # "http://user:pass@proxy_host:port",
    # "socks5://proxy_host:port",
]

# Selenium 配置（处理 JS 渲染的页面，如部分 Springer / IEEE 页面）
USE_SELENIUM = False
SELENIUM_HEADLESS = True
SELENIUM_WAIT_SECONDS = 5       # 等待 JS 渲染完成的秒数

# ─────────────────────────────────────────────
# User-Agent 池（随机轮换，降低被识别概率）
# ─────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ─────────────────────────────────────────────
# 论文结构识别关键词
# ─────────────────────────────────────────────
SECTION_KEYWORDS = {
    "abstract":      ["abstract", "summary"],
    "introduction":  ["introduction", "background", "overview"],
    "related_work":  ["related work", "prior work", "literature review", "background"],
    "methodology":   ["methodology", "method", "approach", "model", "framework",
                      "system", "architecture", "proposed"],
    "experiments":   ["experiment", "evaluation", "result", "benchmark", "dataset",
                      "setup", "implementation"],
    "discussion":    ["discussion", "analysis", "ablation"],
    "conclusion":    ["conclusion", "concluding", "future work", "limitation"],
    "references":    ["references", "bibliography"],
}

# ─────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────
LOG_LEVEL = "INFO"              # DEBUG / INFO / WARNING / ERROR
LOG_TO_FILE = True              # 同时写入日志文件
