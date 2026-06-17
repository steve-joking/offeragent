"""
offeragent — 智能招聘智能体入口

用法:
    # 搜索模式（默认）：搜索 + 抓取 + LLM 筛选 + 入库
    python -m src.main search

    # 投递模式：对筛选后的职位批量投递
    python -m src.main apply

    # 查看统计
    python -m src.main stats

    # Web 界面
    python -m src.main web
"""

import argparse
import asyncio
import logging
import random
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from src.cdp_client import CDPClient
from src.searcher import build_search_urls
from src.scraper import extract_job_list
from src.filter import JobFilter
from src.applicant import Applicant
from src.db import Database
from src.llm import LLMClient

console = Console()
logger = logging.getLogger("offeragent")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    path = PROJECT_ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_resume(cfg: dict) -> str:
    resume_path = cfg.get("resume", {}).get("path", "resume.md")
    path = PROJECT_ROOT / resume_path
    if path.exists():
        return path.read_text(encoding="utf-8")
    console.print(f"[yellow]简历文件不存在: {path}，使用空简历[/]")
    return ""


async def cmd_search(cfg: dict):
    """搜索 + 抓取 + LLM 筛选 + 入库。"""
    keywords = cfg["search"]["keywords"]
    max_pages = cfg["search"]["max_pages"]
    llm_cfg = cfg["llm"]
    threshold = llm_cfg.get("match_threshold", 70)

    resume = load_resume(cfg)
    if not resume.strip():
        console.print("[red]请先在 resume.md 填写简历，或设置 config.yaml 中 resume.path[/]")
        return

    # 初始化组件
    cdp = CDPClient()
    db = Database(str(PROJECT_ROOT / cfg["database"]["path"]))
    llm = LLMClient(
        api_key=llm_cfg.get("api_key", ""),
        base_url=llm_cfg.get("base_url", ""),
        model=llm_cfg.get("model", "gpt-4o-mini"),
    )
    job_filter = JobFilter(llm, resume, threshold)

    console.print(f"[bold green]启动 CDP Proxy...[/]")
    await cdp.start_proxy()
    console.print("[green]CDP Proxy 就绪[/]")

    try:
        urls = build_search_urls(keywords, cfg["search"].get("city", ""))
        total_new = 0

        for entry in urls:
            console.print(f"\n[bold]搜索: {entry['keyword']} ({entry['platform']})[/]")
            tab = await cdp.new_tab(entry["url"])
            await asyncio.sleep(3)  # 等页面加载

            for page in range(1, max_pages + 1):
                console.print(f"  第 {page} 页...")

                # 抓取
                jobs = await extract_job_list(cdp, tab)
                if isinstance(jobs, list):
                    process_jobs = jobs
                else:
                    # 通用模式返回的是文本块，暂不详细解析
                    console.print(f"  [dim]通用模式获取到 {len(jobs.get('cardTexts', []))} 个文本块[/]")
                    break

                if not process_jobs:
                    console.print("  [dim]无更多职位[/]")
                    break

                new_jobs = []
                for job in process_jobs:
                    link = job.get("link", "")
                    if not link or db.job_exists(link):
                        continue
                    job_id = db.insert_job(job, entry["keyword"])
                    if job_id:
                        job["_db_id"] = job_id
                        new_jobs.append(job)

                console.print(f"  新增 {len(new_jobs)} 个职位")

                # LLM 匹配
                if new_jobs:
                    console.print(f"  LLM 筛选中 ({len(new_jobs)} 个)...")
                    match_results = await job_filter.batch_match(new_jobs)
                    for mr in match_results:
                        job_id = mr["job"].get("_db_id")
                        if job_id:
                            db.insert_score(job_id, mr)
                    console.print(f"  匹配 {len(match_results)} 个合格职位")

                total_new += len(new_jobs)

                # 翻页
                try:
                    await cdp.click(tab, "a:has-text('下一页')")
                    await asyncio.sleep(2 + random.uniform(0, 2))
                except Exception:
                    console.print("  [dim]无法翻页[/]")
                    break

            await cdp.close_tab(tab)

        # 展示统计
        stats = db.stats()
        console.print(f"\n[bold green]搜索完成[/]")
        table = Table(title="统计")
        table.add_column("指标", style="cyan")
        table.add_column("数量", style="green")
        table.add_row("总职位数", str(stats["total_jobs"]))
        table.add_row("合格候选", str(stats["candidates"]))
        table.add_row("已投递", str(stats["total_applied"]))
        table.add_row("本次新增", str(total_new))
        console.print(table)

    finally:
        await cdp.close()
        db.close()


async def cmd_apply(cfg: dict):
    """对筛选后的职位批量投递。"""
    apply_cfg = cfg["apply"]
    if not apply_cfg.get("enabled"):
        console.print("[red]投递功能未启用，请在 config.yaml 中设置 apply.enabled: true[/]")
        return

    cdp = CDPClient()
    db = Database(str(PROJECT_ROOT / cfg["database"]["path"]))
    applicant = Applicant(cdp, interval=apply_cfg.get("interval_seconds", 5))

    pending = db.get_pending_applications(limit=apply_cfg.get("max_per_day", 30))
    if not pending:
        console.print("[yellow]没有待投递的职位[/]")
        return

    console.print(f"[bold]待投递 {len(pending)} 个职位[/]")
    console.print("[yellow]⚠ 即将开始自动投递，请确保已在 Chrome 中登录招聘网站[/]")
    input("按 Enter 继续...")

    console.print("[bold green]启动 CDP Proxy...[/]")
    await cdp.start_proxy()

    try:
        jobs = [dict(row) for row in pending]
        success, fail = await applicant.apply_batch(
            jobs, max_per_day=apply_cfg.get("max_per_day", 30)
        )

        # 记录投递状态
        for job in jobs[:success]:
            db.record_application(job["id"], "applied")

        console.print(f"\n[bold green]投递完成: 成功 {success}, 失败 {fail}[/]")
    finally:
        await cdp.close()
        db.close()


def cmd_stats(cfg: dict):
    """查看统计。"""
    db = Database(str(PROJECT_ROOT / cfg["database"]["path"]))
    stats = db.stats()
    table = Table(title="offeragent 统计")
    table.add_column("指标", style="cyan")
    table.add_column("数量", style="green")
    table.add_row("总职位数", str(stats["total_jobs"]))
    table.add_row("合格候选", str(stats["candidates"]))
    table.add_row("已投递", str(stats["total_applied"]))
    console.print(table)
    db.close()


def main():
    parser = argparse.ArgumentParser(description="offeragent — 智能招聘智能体")
    parser.add_argument(
        "command",
        nargs="?",
        default="search",
        choices=["search", "apply", "stats", "web"],
        help="执行命令: search(搜索筛选) / apply(投递) / stats(统计) / web(Web界面)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Web 服务器地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8800, help="Web 服务器端口 (默认: 8800)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config()

    if args.command == "search":
        asyncio.run(cmd_search(cfg))
    elif args.command == "apply":
        asyncio.run(cmd_apply(cfg))
    elif args.command == "stats":
        cmd_stats(cfg)
    elif args.command == "web":
        from src.web import start_server
        start_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
