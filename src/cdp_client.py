"""
CDP Proxy HTTP 客户端 — 封装对 localhost:3456 的所有 API 调用。
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CDP_SCRIPTS = PROJECT_ROOT / "scripts" / "cdp-proxy"
CHECK_DEPS = CDP_SCRIPTS / "check-deps.mjs"
PROXY_PORT = 3456
BASE_URL = f"http://localhost:{PROXY_PORT}"

# Node.js 路径（自动检测，兼容不同环境）
_NODE_PATHS = [
    Path("/Users/haibiaoliu/.workbuddy/binaries/node/versions/22.22.2/bin/node"),
    "/opt/homebrew/bin/node",
    "/usr/local/bin/node",
    "/usr/bin/node",
]
NODE_PATH: str | None = None
for p in _NODE_PATHS:
    if isinstance(p, Path) and p.is_file():
        NODE_PATH = str(p)
        break
    elif isinstance(p, str) and Path(p).is_file():
        NODE_PATH = p
        break
if not NODE_PATH:
    import shutil
    _node_shutil = shutil.which("node")
    if _node_shutil:
        NODE_PATH = _node_shutil


class CDPClient:
    """浏览器 CDP Proxy 的 Python 客户端。"""

    def __init__(self, browser: str = "chrome"):
        self.browser = browser
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30))

    # ---- 生命周期 ----

    async def is_proxy_alive(self) -> bool:
        """检查 CDP Proxy 是否已在运行。"""
        try:
            r = await self._client.get(f"{BASE_URL}/health", timeout=3)
            data = r.json()
            return data.get("status") == "ok" and data.get("connected") is True
        except Exception:
            return False

    async def start_proxy(self) -> bool:
        """启动 CDP Proxy（如已运行则复用，否则通过 check-deps.mjs 启动）。"""
        # 先检查是否已在运行
        if await self.is_proxy_alive():
            return True

        node = NODE_PATH or "node"
        # 用 asyncio 子进程避免阻塞事件循环
        proc = await asyncio.create_subprocess_exec(
            node, str(CHECK_DEPS), "--browser", self.browser,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("CDP Proxy 启动超时 (30s)")

        if proc.returncode != 0:
            raise RuntimeError(
                f"CDP Proxy 启动失败 (exit {proc.returncode}):\n"
                f"{stdout.decode()}\n{stderr.decode()}"
            )
        # 等待 proxy 完全就绪
        for i in range(15):
            if await self.is_proxy_alive():
                return True
            await asyncio.sleep(0.5)
        raise RuntimeError("CDP Proxy 启动后无法连接")

    async def close(self):
        await self._client.aclose()

    # ---- Tab 管理 ----

    async def new_tab(self, url: str) -> str:
        """创建新 tab，返回 targetId。"""
        r = await self._client.post(f"{BASE_URL}/new", content=url.encode())
        r.raise_for_status()
        return r.json()["targetId"]

    async def close_tab(self, target_id: str) -> None:
        r = await self._client.get(f"{BASE_URL}/close", params={"target": target_id})
        r.raise_for_status()

    async def list_tabs(self) -> Any:
        r = await self._client.get(f"{BASE_URL}/targets")
        r.raise_for_status()
        return r.json()

    # ---- 导航 ----

    async def navigate(self, target_id: str, url: str) -> None:
        r = await self._client.post(
            f"{BASE_URL}/navigate",
            params={"target": target_id},
            content=url.encode(),
        )
        r.raise_for_status()

    async def info(self, target_id: str) -> dict:
        r = await self._client.get(f"{BASE_URL}/info", params={"target": target_id})
        r.raise_for_status()
        return r.json()

    # ---- 脚本执行 ----

    async def eval(self, target_id: str, code: str) -> Any:
        """在页面中执行 JS 代码并返回结果。"""
        r = await self._client.post(
            f"{BASE_URL}/eval",
            params={"target": target_id},
            content=code.encode(),
        )
        r.raise_for_status()
        result = r.json()
        # 返回值在 {"value": ...} 中
        if isinstance(result, dict) and "value" in result:
            return result["value"]
        return result

    # ---- 交互 ----

    async def click(self, target_id: str, selector: str) -> None:
        r = await self._client.post(
            f"{BASE_URL}/click",
            params={"target": target_id},
            content=selector.encode(),
        )
        r.raise_for_status()

    async def click_at(self, target_id: str, selector: str) -> None:
        """真实鼠标点击（用于触发文件对话框等）。"""
        r = await self._client.post(
            f"{BASE_URL}/clickAt",
            params={"target": target_id},
            content=selector.encode(),
        )
        r.raise_for_status()

    async def scroll(self, target_id: str, y: int = 1000) -> None:
        r = await self._client.get(f"{BASE_URL}/scroll", params={"target": target_id, "y": str(y)})
        r.raise_for_status()

    # ---- 截图 ----

    async def screenshot(self, target_id: str, filepath: str = None) -> bytes | None:
        params = {"target": target_id}
        if filepath:
            params["file"] = filepath
        r = await self._client.get(f"{BASE_URL}/screenshot", params=params)
        r.raise_for_status()
        return r.content
