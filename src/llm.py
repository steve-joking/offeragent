"""
LLM 调用模块 — 统一的 LLM 接口封装，支持 OpenAI / Azure / 自定义 API。
"""

import os
import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """LLM 客户端，兼容 OpenAI 接口协议。"""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "gpt-4o-mini",
    ):
        api_key = api_key or os.getenv("OPENAI_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )
        self.model = model

    async def chat(self, prompt: str, system_prompt: str = "") -> str:
        """发送聊天请求，返回文本回复。"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.1,  # 低温度保证结果稳定
        )
        content = response.choices[0].message.content
        logger.debug(f"LLM 响应长度: {len(content) if content else 0}")
        return content or ""
