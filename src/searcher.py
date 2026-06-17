"""
搜索模块 — 在招聘平台上搜索指定关键词的职位。
"""

import time
import logging
from src.cdp_client import CDPClient

logger = logging.getLogger(__name__)

# 智联招聘搜索结果 URL 模式
ZHAOPIN_SEARCH_URL = "https://sou.zhaopin.com/?kw={keyword}"
# 备用：site 内搜索结果
ZHAOPIN_SITE_SEARCH = "https://www.zhaopin.com/sou/?keyword={keyword}"


def build_search_urls(keywords: list[str], city: str = "") -> list[dict]:
    """生成各平台的搜索链接列表。"""
    urls = []
    for kw in keywords:
        encoded = _encode_kw(kw)
        urls.append({
            "platform": "zhaopin",
            "keyword": kw,
            "url": f"https://sou.zhaopin.com/?kw={encoded}",
        })
    return urls


def _encode_kw(keyword: str) -> str:
    """将中文关键词转为 URL 编码，智联招聘用 kw 参数。"""
    import urllib.parse
    return urllib.parse.quote(keyword)


async def search_and_get_page_info(
    client: CDPClient, target_id: str
) -> dict:
    """获取当前搜索结果页的基本信息。"""
    info = await client.info(target_id)
    return {"url": info.get("url"), "title": info.get("title")}
