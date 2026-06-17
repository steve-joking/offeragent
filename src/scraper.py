"""
抓取模块 — 从浏览器 DOM 中提取职位结构化数据。
"""

import json
import logging
from typing import Any

from src.cdp_client import CDPClient

logger = logging.getLogger(__name__)

EXTRACT_JOBS_JS = """
(() => {
  const items = document.querySelectorAll('.joblist-box__item');
  const results = [];
  items.forEach(el => {
    // 智联招聘 2026 年最新版选择器
    const title = el.querySelector('.jobinfo__name')?.textContent?.trim() || '';
    const salary = el.querySelector('.jobinfo__salary')?.textContent?.trim() || '';
    // company：智联 2026 用 companyinfo__name
    const company = el.querySelector('.companyinfo__name')?.textContent?.trim() || '';
    // location / experience / education 从 other-info-item 提取
    const metaItems = el.querySelectorAll('.jobinfo__other-info-item');
    const location = metaItems[0]?.textContent?.trim() || '';
    const experience = metaItems[1]?.textContent?.trim() || '';
    const education = metaItems[2]?.textContent?.trim() || '';
    // 标签
    const tags = Array.from(el.querySelectorAll('.jobinfo__tag, .joblist-box__item-tag span')).map(t => t.textContent.trim());
    // 链接
    const link = el.querySelector('a')?.href || '';
    if (title) {
      results.push({ title, salary, company, location, experience, education, desc: '', tags, link });
    }
  });
  return JSON.stringify(results);
})()
"""

EXTRACT_JOBS_GENERIC_JS = """
(() => {
  // 通用提取方案 —— 遍历所有包含职位关键信息的容器
  const pageText = document.body.innerText;
  const result = {
    pageText: pageText.substring(0, 5000),
    url: location.href,
    title: document.title,
    // 尝试直接提取职位卡片文本
    cardTexts: []
  };
  document.querySelectorAll('[class*=joblist], [class*=jobList], [class*=position]').forEach(el => {
    const text = el.textContent.trim();
    if (text.length > 30 && text.length < 1000) {
      result.cardTexts.push(text.substring(0, 500));
    }
  });
  return JSON.stringify(result);
})()
"""


async def extract_job_list(client: CDPClient, target_id: str) -> list[dict[str, Any]]:
    """从当前页面提取职位列表。先尝试精准选择器，失败则用通用方案。"""
    try:
        raw = await client.eval(target_id, EXTRACT_JOBS_JS)
        jobs = json.loads(raw) if isinstance(raw, str) else raw
        if jobs and len(jobs) > 0:
            logger.info(f"精准提取到 {len(jobs)} 个职位")
            return jobs
    except (json.JSONDecodeError, KeyError) as e:
        logger.debug(f"精准提取失败: {e}")

    # 回退到通用方案
    logger.info("回退到通用提取方案")
    raw = await client.eval(target_id, EXTRACT_JOBS_GENERIC_JS)
    data = json.loads(raw) if isinstance(raw, str) else raw
    logger.info(f"通用提取到 {len(data.get('cardTexts', []))} 个文本块")
    return data  # 返回原始数据，由调用方解析
