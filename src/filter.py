"""
筛选引擎 — 使用 LLM 解析 JD 并与简历匹配打分。
"""

import json
import logging

from src.llm import LLMClient

logger = logging.getLogger(__name__)

MATCH_PROMPT = """你是一个专业的招聘匹配分析师。请根据求职者的简历和职位描述，评估匹配度。

## 评分标准 (0-100)
- 90-100: 高度匹配，核心技能 + 经验完全吻合
- 70-89: 良好匹配，主要技能对应，经验略有差距
- 50-69: 部分匹配，有基础能力但缺少关键经验
- 0-49: 不匹配，技能树差异较大

## 输出格式
只输出一个 JSON 对象，不包含其他内容：
{{"score": 85, "reasons": ["技能高度匹配", "项目经验相关"], "risks": ["期望可能不匹配"], "recommend": true}}

## 简历
{resume}

## 职位描述
{job_description}
"""


class JobFilter:
    """职位筛选器。"""

    def __init__(self, llm: LLMClient, resume: str, threshold: int = 70):
        self.llm = llm
        self.resume = resume
        self.threshold = threshold

    async def match(self, job: dict) -> dict | None:
        """匹配单个职位，返回评分结果或 None（低于阈值）。"""
        jd = job.get("desc", "") or job.get("pageText", "") or str(job)
        if not jd or len(jd) < 20:
            logger.debug(f"JD 内容过短，跳过: {job.get('title', '')}")
            return None

        prompt = MATCH_PROMPT.format(resume=self.resume, job_description=jd)
        try:
            response = await self.llm.chat(prompt)
            result = json.loads(response)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"LLM 解析失败: {e}")
            return None

        result["job"] = job
        logger.info(
            f"[{result.get('score', 0)}分] {job.get('title', 'N/A')} "
            f"| {job.get('company', 'N/A')} | {job.get('salary', 'N/A')}"
        )
        return result if result.get("score", 0) >= self.threshold else None

    async def batch_match(self, jobs: list[dict]) -> list[dict]:
        """批量匹配职位。"""
        if not jobs:
            return []

        results = []
        for job in jobs:
            match = await self.match(job)
            if match:
                results.append(match)

        # 按分数降序
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return results
