"""
数据库模块 — SQLite 持久化层，记录职位和投递状态。
"""

import sqlite3
import time
from pathlib import Path
from typing import Any


class Database:
    """SQLite 数据库操作封装。"""

    def __init__(self, db_path: str = "data/offeragent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        cur = self._conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL DEFAULT 'zhaopin',
                title TEXT,
                company TEXT,
                salary TEXT,
                location TEXT,
                experience TEXT,
                education TEXT,
                description TEXT,
                link TEXT UNIQUE,
                keywords TEXT,
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS job_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER REFERENCES jobs(id),
                score INTEGER,
                reasons TEXT,
                risks TEXT,
                recommend INTEGER,
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER REFERENCES jobs(id),
                status TEXT DEFAULT 'pending',
                applied_at REAL DEFAULT (strftime('%s','now')),
                note TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_link ON jobs(link);
            CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
            CREATE INDEX IF NOT EXISTS idx_scores_job ON job_scores(job_id);
        """)
        self._conn.commit()

    # ---- 职位 CRUD ----

    def insert_job(self, job: dict[str, Any], keywords: str = "") -> int | None:
        """插入职位，已存在则跳过。返回 job_id 或 None。"""
        try:
            cur = self._conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO jobs (platform, title, company, salary,
                    location, experience, education, description, link, keywords)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.get("platform", "zhaopin"),
                job.get("title", ""),
                job.get("company", ""),
                job.get("salary", ""),
                job.get("location", ""),
                job.get("experience", ""),
                job.get("education", ""),
                job.get("desc", ""),
                job.get("link", ""),
                keywords,
            ))
            self._conn.commit()
            return cur.lastrowid if cur.rowcount > 0 else None
        except Exception as e:
            self._conn.rollback()
            return None

    def job_exists(self, link: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("SELECT 1 FROM jobs WHERE link = ?", (link,))
        return cur.fetchone() is not None

    # ---- 评分 ----

    def insert_score(self, job_id: int, match_result: dict) -> None:
        cur = self._conn.cursor()
        cur.execute("""
            INSERT INTO job_scores (job_id, score, reasons, risks, recommend)
            VALUES (?, ?, ?, ?, ?)
        """, (
            job_id,
            match_result.get("score"),
            str(match_result.get("reasons", [])),
            str(match_result.get("risks", [])),
            1 if match_result.get("recommend") else 0,
        ))
        self._conn.commit()

    # ---- 投递 ----

    def record_application(self, job_id: int, status: str = "applied") -> None:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO applications (job_id, status) VALUES (?, ?)",
            (job_id, status),
        )
        self._conn.commit()

    def get_pending_applications(self, limit: int = 50) -> list[sqlite3.Row]:
        """获取待投递的职位列表（按分数降序）。"""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT j.*, s.score, s.reasons, s.recommend
            FROM jobs j
            JOIN job_scores s ON s.job_id = j.id
            LEFT JOIN applications a ON a.job_id = j.id
            WHERE a.id IS NULL AND s.score >= 70
            ORDER BY s.score DESC
            LIMIT ?
        """, (limit,))
        return cur.fetchall()

    def already_applied(self, link: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("""
            SELECT 1 FROM jobs j
            JOIN applications a ON a.job_id = j.id
            WHERE j.link = ?
        """, (link,))
        return cur.fetchone() is not None

    # ---- 统计 ----

    def stats(self) -> dict:
        cur = self._conn.cursor()
        stats = {}
        cur.execute("SELECT COUNT(*) FROM jobs")
        stats["total_jobs"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM applications WHERE status='applied'")
        stats["total_applied"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM job_scores WHERE score >= 70")
        stats["candidates"] = cur.fetchone()[0]
        return stats

    def close(self):
        self._conn.close()
