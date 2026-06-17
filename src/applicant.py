"""
投递模块 — 自动在招聘平台上点击"立即投递"并处理确认弹窗。
"""

import asyncio
import random
import logging

from src.cdp_client import CDPClient

logger = logging.getLogger(__name__)

# 投递按钮的常见选择器（按优先级）
APPLY_SELECTORS = [
    "button:has-text('立即投递')",
    ".apply-btn",
    '[class*="apply"]',
    '[class*="send"]',
    "button:has-text('投递简历')",
]

# 确认弹窗按钮
CONFIRM_SELECTORS = [
    ".dialog-ok",
    ".confirm-btn",
    "button:has-text('确定')",
    "button:has-text('确认')",
]


class Applicant:
    """职位投递器。"""

    def __init__(self, client: CDPClient, interval: float = 5):
        self.client = client
        self.interval = interval

    async def apply(self, target_id: str, job: dict) -> bool:
        """投递一个职位。返回是否投递成功。"""
        job_title = job.get("title", "Unknown")
        job_url = job.get("link", "")
        logger.info(f"开始投递: {job_title}")

        # 1. 如果有关联链接，先导航到详情页
        if job_url:
            await self.client.navigate(target_id, job_url)
            await asyncio.sleep(2)

        # 2. 尝试点击投递按钮（多种选择器兜底）
        clicked = False
        for sel in APPLY_SELECTORS:
            try:
                await self.client.click(target_id, sel)
                clicked = True
                logger.debug(f"投递按钮点击成功: {sel}")
                break
            except Exception:
                continue

        if not clicked:
            logger.warning(f"未找到投递按钮: {job_title}")
            return False

        # 3. 等待弹窗出现并确认
        await asyncio.sleep(1)
        for sel in CONFIRM_SELECTORS:
            try:
                await self.client.click(target_id, sel)
                logger.info(f"投递确认成功: {job_title}")
                return True
            except Exception:
                continue

        # 无弹窗时可能直接投递成功了
        logger.info(f"投递完成（无弹窗）: {job_title}")
        return True

    async def apply_batch(
        self, jobs: list[dict], max_per_day: int = 30
    ) -> tuple[int, int]:
        """批量投递职位。返回 (成功数, 失败数)。"""
        success, fail = 0, 0

        tab = await self.client.new_tab("about:blank")
        try:
            for i, job in enumerate(jobs[:max_per_day]):
                ok = await self.apply(tab, job)
                if ok:
                    success += 1
                else:
                    fail += 1

                # 随机间隔防反爬
                delay = self.interval + random.uniform(1, 5)
                logger.debug(f"等待 {delay:.1f}s")
                await asyncio.sleep(delay)
        finally:
            await self.client.close_tab(tab)

        return success, fail
