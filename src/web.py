"""
offeragent Web 界面 — FastAPI + 内嵌单页应用。
"""

import asyncio
import logging
import random
import threading
import time
import json
import traceback
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from src.cdp_client import CDPClient
from src.searcher import build_search_urls
from src.scraper import extract_job_list
from src.filter import JobFilter
from src.applicant import Applicant
from src.db import Database
from src.llm import LLMClient

logger = logging.getLogger("offeragent.web")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── 全局状态 ──────────────────────────────────────────────

_search_lock = threading.Lock()
_search_log: list[str] = []
_search_state: dict = {
    "phase": "idle",
    "phase_label": "就绪",
    "total_steps": 0,
    "current_step": 0,
    "keyword": "",
    "page": 0,
    "max_pages": 0,
    "total_found": 0,
    "new_this_round": 0,
    "elapsed": 0,
    "start_time": None,
    "error": None,
}


def reset_search_state():
    global _search_state
    _search_state = {
        "phase": "idle",
        "phase_label": "就绪",
        "total_steps": 0,
        "current_step": 0,
        "keyword": "",
        "page": 0,
        "max_pages": 0,
        "total_found": 0,
        "new_this_round": 0,
        "elapsed": 0,
        "start_time": None,
        "error": None,
    }


def add_log(msg: str):
    ts = time.strftime("%H:%M:%S")
    _search_log.append(f"[{ts}] {msg}")
    if len(_search_log) > 200:
        _search_log.pop(0)


def load_config_sync() -> dict:
    path = PROJECT_ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_db() -> Database:
    cfg = load_config_sync()
    return Database(str(PROJECT_ROOT / cfg["database"]["path"]))


# ── FastAPI App ───────────────────────────────────────────

app = FastAPI(title="offeragent", version="1.0")


# ── Pydantic Models ───────────────────────────────────────

class SearchRequest(BaseModel):
    keywords: Optional[str] = None
    max_pages: int = 3


class ApplyRequest(BaseModel):
    job_id: int


# ── HTML SPA ──────────────────────────────────────────────

SPA_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate, max-age=0">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>offeragent — 智能招聘</title>
<style>
:root {
  --bg: #1a1a2e;
  --card: #16213e;
  --border: #0f3460;
  --accent: #e94560;
  --text: #eaeaea;
  --dim: #a0a0b0;
  --green: #00c853;
  --yellow: #ffd600;
  --red: #ff1744;
  --blue: #448aff;
  --radius: 10px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }

/* Header */
.header { display: flex; align-items: center; justify-content: space-between; padding: 20px 32px; border-bottom: 1px solid var(--border); }
.header h1 { font-size: 22px; font-weight: 700; letter-spacing: -0.5px; }
.header h1 span { color: var(--accent); }
.header-actions { display: flex; gap: 10px; }

/* Layout */
.container { max-width: 1280px; margin: 0 auto; padding: 24px 32px; }

/* Stats cards */
.stats-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
.stat-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
.stat-card .label { font-size: 13px; color: var(--dim); margin-bottom: 8px; }
.stat-card .value { font-size: 32px; font-weight: 700; }
.stat-card .value.green { color: var(--green); }
.stat-card .value.yellow { color: var(--yellow); }
.stat-card .value.blue { color: var(--blue); }
.stat-card .value.red { color: var(--accent); }

/* Toolbar */
.toolbar { display: flex; gap: 12px; margin-bottom: 20px; align-items: center; flex-wrap: wrap; }
.toolbar select, .toolbar input { padding: 8px 14px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 14px; outline: none; }
.toolbar select:focus, .toolbar input:focus { border-color: var(--accent); }
.toolbar input { flex: 1; min-width: 200px; }

/* Buttons */
.btn { padding: 10px 20px; border: none; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { opacity: 0.85; transform: translateY(-1px); }
.btn-secondary { background: var(--border); color: var(--text); }
.btn-secondary:hover { background: #1a4a8a; }
.btn-success { background: var(--green); color: #000; }
.btn-success:hover { opacity: 0.85; }
.btn-danger { background: #c0392b; color: #fff; }
.btn-danger:hover { background: #e74c3c; transform: translateY(-1px); }
.btn-sm { padding: 6px 14px; font-size: 12px; }
.btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

/* Table */
.table-wrap { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; padding: 14px 16px; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--dim); background: rgba(0,0,0,0.2); border-bottom: 1px solid var(--border); }
td { padding: 12px 16px; font-size: 14px; border-bottom: 1px solid rgba(15,52,96,0.3); }
tr:hover td { background: rgba(233,69,96,0.05); }
.score-badge { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 12px; font-weight: 700; }
.score-high { background: rgba(0,200,83,0.2); color: var(--green); }
.score-mid { background: rgba(255,214,0,0.2); color: var(--yellow); }
.score-low { background: rgba(255,23,68,0.2); color: var(--red); }
.salary { color: var(--accent); font-weight: 600; white-space: nowrap; }
.company { font-weight: 600; }
.link { color: var(--blue); text-decoration: none; font-size: 13px; }
.link:hover { text-decoration: underline; }
.status { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.status-applied { background: rgba(0,200,83,0.2); color: var(--green); }
.status-pending { background: rgba(68,138,255,0.2); color: var(--blue); }

/* Log */
.log-section { margin-top: 32px; }
.log-section h3 { font-size: 16px; margin-bottom: 12px; color: var(--dim); }
.log-box { background: #0d1117; border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; max-height: 300px; overflow-y: auto; font-family: "SF Mono", "Fira Code", monospace; font-size: 12px; line-height: 1.6; color: var(--dim); }

/* Search running indicator */
.running { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 1.5s infinite; margin-right: 8px; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

/* Progress bar */
.progress-section { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; margin-bottom: 20px; display: none; }
.progress-section.visible { display: block; }
.progress-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.progress-header .phase-label { font-size: 14px; font-weight: 600; color: var(--text); }
.progress-header .step-info { font-size: 12px; color: var(--dim); }
.progress-track { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; margin-bottom: 8px; }
.progress-fill { height: 100%; background: linear-gradient(90deg, var(--accent), var(--blue)); border-radius: 3px; transition: width 0.6s ease; width: 0%; }
.progress-stats { display: flex; gap: 20px; font-size: 12px; color: var(--dim); }
.progress-stats span strong { color: var(--accent); }

/* Pagination */
.pagination { display: flex; gap: 8px; justify-content: center; margin: 20px 0; }
.pagination button { padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border); background: var(--card); color: var(--text); cursor: pointer; font-size: 13px; }
.pagination button.active { background: var(--accent); border-color: var(--accent); }
.pagination button:hover:not(.active) { background: var(--border); }
.page-ellipsis { display: inline-flex; align-items: center; padding: 0 4px; color: var(--dim); font-size: 13px; }

/* Empty */
.empty { text-align: center; padding: 60px 20px; color: var(--dim); }
.empty .icon { font-size: 48px; margin-bottom: 16px; }

/* Spinner */
.spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.2); border-top-color: #fff; border-radius: 50%; animation: spin 0.6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Toast */
.toast { position: fixed; top: 20px; right: 20px; padding: 12px 24px; border-radius: 8px; font-size: 14px; font-weight: 600; z-index: 1000; animation: slideIn 0.3s ease; }
.toast.success { background: var(--green); color: #000; }
.toast.error { background: var(--red); color: #fff; }
.toast.info { background: var(--blue); color: #fff; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

/* Responsive */
@media (max-width: 768px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .header { flex-direction: column; gap: 12px; }
  .container { padding: 16px; }
  th:nth-child(4), td:nth-child(4) { display: none; }
}
</style>
</head>
<body>
<div class="header">
  <h1>🎯 offer<span>agent</span></h1>
  <div class="header-actions">
    <button class="btn btn-danger btn-sm" onclick="clearAllData()" title="清空所有职位、评分和投递记录">🗑 清空</button>
    <button class="btn btn-secondary btn-sm" onclick="refreshAll()">🔄 刷新</button>
  </div>
</div>

<div class="container">
  <!-- Stats -->
  <div class="stats-row" id="stats">
    <div class="stat-card"><div class="label">抓取职位</div><div class="value blue">—</div></div>
    <div class="stat-card"><div class="label">合格候选</div><div class="value yellow">—</div></div>
    <div class="stat-card"><div class="label">已投递</div><div class="value green">—</div></div>
    <div class="stat-card"><div class="label">今日投递</div><div class="value red">—</div></div>
  </div>

  <!-- Toolbar -->
  <div class="toolbar">
    <select id="platformFilter" onchange="loadJobs()">
      <option value="">全部平台</option>
      <option value="zhaopin">智联招聘</option>
      <option value="boss">Boss 直聘</option>
      <option value="liepin">猎聘</option>
    </select>
    <select id="scoreFilter" onchange="loadJobs()">
      <option value="">全部评分</option>
      <option value="80">≥80 强烈推荐</option>
      <option value="70">≥70 推荐</option>
      <option value="60">≥60 可考虑</option>
      <option value="0">未评分</option>
    </select>
    <input type="text" id="keywordInput" placeholder="搜索关键词，如：Java后端开发、Python..." style="flex:1;min-width:200px;" onkeydown="if(event.key==='Enter')triggerSearch()">
    <button id="btnSearch" class="btn btn-primary" onclick="triggerSearch()">🔍 开始搜索</button>
    <button id="btnSearching" class="btn btn-primary" disabled style="display:none"><span class="spinner"></span> 搜索中...</button>
  </div>
  <div class="toolbar" style="margin-top:8px">
    <input type="text" id="searchInput" placeholder="筛选公司/职位（本地过滤）..." oninput="filterTable()" style="width:100%">
  </div>

  <!-- Progress -->
  <div class="progress-section" id="progressSection">
    <div class="progress-header">
      <span class="phase-label" id="progressLabel">准备中...</span>
      <span class="step-info" id="progressSteps">—</span>
    </div>
    <div class="progress-track">
      <div class="progress-fill" id="progressFill"></div>
    </div>
    <div class="progress-stats">
      <span>本轮新增: <strong id="progressNew">0</strong> 个</span>
      <span>累计找到: <strong id="progressTotal">0</strong> 个</span>
      <span>耗时: <strong id="progressElapsed">0s</strong></span>
    </div>
  </div>

  <!-- Job Table -->
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th style="width:50px">#</th>
          <th>职位</th>
          <th>公司</th>
          <th>城市</th>
          <th>薪资</th>
          <th>经验</th>
          <th style="width:70px">评分</th>
          <th style="width:100px">操作</th>
        </tr>
      </thead>
      <tbody id="jobTable">
        <tr><td colspan="8" class="empty"><div class="icon">📋</div>暂无数据，点击「开始搜索」获取职位</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Pagination -->
  <div class="pagination" id="pagination"></div>

  <!-- Search Log -->
  <div class="log-section">
    <h3>📜 搜索日志</h3>
    <div class="log-box" id="logBox">等待操作...</div>
  </div>
</div>

<script>
const POLL_INTERVAL = 3000;

// ── Notifications ────────────────────────────────────────
function toast(msg, type='info') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ── API ──────────────────────────────────────────────────
async function api(path, opts={}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({detail: r.statusText}));
    throw new Error(err.detail || '请求失败');
  }
  return r.json();
}

// ── Stats ────────────────────────────────────────────────
async function loadStats() {
  const s = await api('/api/stats');
  const cards = document.querySelectorAll('#stats .value');
  cards[0].textContent = s.total_jobs.toLocaleString();
  cards[1].textContent = s.candidates.toLocaleString();
  cards[2].textContent = s.total_applied.toLocaleString();
  cards[3].textContent = s.today_applied.toLocaleString();
}

// ── Jobs ─────────────────────────────────────────────────
let currentPage = 1;
const pageSize = 20;

async function loadJobs(page=1) {
  currentPage = page;
  const platform = document.getElementById('platformFilter').value;
  const minScore = document.getElementById('scoreFilter').value;
  const params = new URLSearchParams({ offset: (page-1)*pageSize, limit: pageSize });
  if (platform) params.set('platform', platform);
  if (minScore) params.set('min_score', minScore);

  const data = await api('/api/jobs?' + params);
  renderJobs(data.items);
  renderPagination(data.total, page);
  filterTable(); // apply text search filter
}

function renderJobs(jobs) {
  const tbody = document.getElementById('jobTable');
  if (!jobs.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty"><div class="icon">📭</div>暂无匹配职位</td></tr>';
    return;
  }
  tbody.innerHTML = jobs.map((j, i) => {
    const scoreClass = j.score >= 80 ? 'score-high' : j.score >= 60 ? 'score-mid' : 'score-low';
    const scoreText = j.score ? `<span class="score-badge ${scoreClass}">${j.score}</span>` : '<span style="color:var(--dim)">—</span>';
    const appliedBadge = j.applied ? '<span class="status status-applied">已投递</span>' : '';
    const applyBtn = !j.applied && j.score >= 70
      ? `<button class="btn btn-success btn-sm" onclick="applyJob(${j.id})">🚀 投递</button>`
      : j.applied
        ? '<span class="status status-applied">已投递</span>'
        : '<button class="btn btn-secondary btn-sm" disabled>待评分</button>';
    const linkHtml = j.link ? `<a class="link" href="${escapeHtml(j.link)}" target="_blank" title="在新标签页打开">🔗</a>` : '';

    return `<tr data-search="${escapeHtml((j.title||'') + ' ' + (j.company||'') + ' ' + (j.location||'') + ' ' + (j.salary||'')).toLowerCase()}" data-row-index="${i}">
      <td class="row-num" style="color:var(--dim)">${(currentPage-1)*pageSize + i + 1}</td>
      <td><div class="company">${escapeHtml(j.title||'—')}</div>${linkHtml}</td>
      <td style="color:var(--dim)">${escapeHtml(j.company||'—')}</td>
      <td>${escapeHtml(j.location||'—')}</td>
      <td class="salary">${escapeHtml(j.salary||'—')}</td>
      <td style="font-size:13px;color:var(--dim)">${escapeHtml(j.experience||'—')}</td>
      <td>${scoreText}</td>
      <td>${appliedBadge}${applyBtn}</td>
    </tr>`;
  }).join('');
}

function renderPagination(total, page) {
  const pages = Math.ceil(total / pageSize);
  const wrap = document.getElementById('pagination');
  if (pages <= 1) { wrap.innerHTML = ''; return; }

  // 智能分页：最多显示 7 个按钮 + 省略号
  const maxVisible = 7;
  let nums = [];
  if (pages <= maxVisible + 2) {
    for (let p = 1; p <= pages; p++) nums.push(p);
  } else {
    // 始终显示: 1 ... current附近 ... last
    nums.push(1);
    const start = Math.max(2, page - 2);
    const end = Math.min(pages - 1, page + 2);
    if (start > 2) nums.push('...');
    for (let p = start; p <= end; p++) nums.push(p);
    if (end < pages - 1) nums.push('...');
    nums.push(pages);
  }

  let html = '';
  nums.forEach(p => {
    if (p === '...') {
      html += '<span class="page-ellipsis">…</span>';
    } else {
      html += `<button class="${p === page ? 'active' : ''}" onclick="loadJobs(${p})">${p}</button>`;
    }
  });
  wrap.innerHTML = html;
}

function filterTable() {
  const q = document.getElementById('searchInput').value.toLowerCase();
  let visibleIndex = 0;
  document.querySelectorAll('#jobTable tr[data-search]').forEach(tr => {
    const show = q === '' || tr.dataset.search.includes(q);
    tr.style.display = show ? '' : 'none';
    if (show) {
      visibleIndex++;
      const numCell = tr.querySelector('.row-num');
      if (numCell) numCell.textContent = visibleIndex;
    }
  });
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── Apply ────────────────────────────────────────────────
async function applyJob(jobId) {
  if (!confirm('确认投递此职位吗？')) return;
  try {
    const r = await api('/api/apply/' + jobId, { method: 'POST' });
    toast('投递成功！', 'success');
    loadJobs(currentPage);
    loadStats();
  } catch(e) {
    toast('投递失败: ' + e.message, 'error');
  }
}

// ── Search ───────────────────────────────────────────────
async function triggerSearch() {
  const btn = document.getElementById('btnSearch');
  const btnSearching = document.getElementById('btnSearching');

  const kwInput = document.getElementById('keywordInput');
  const keyword = (kwInput.value || '').trim();
  if (!keyword) {
    toast('请输入搜索关键词', 'error');
    kwInput.focus();
    return;
  }

  btn.style.display = 'none';
  btnSearching.style.display = 'inline-flex';

  // 立即显示进度条
  window._progressDismissed = false;
  const section = document.getElementById('progressSection');
  section.classList.add('visible');
  document.getElementById('progressLabel').textContent = '正在启动搜索...';
  document.getElementById('progressFill').style.width = '2%';

  try {
    await api('/api/search', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({keywords: keyword}) });
    toast('搜索已启动：' + keyword, 'info');
  } catch(e) {
    toast('启动搜索失败: ' + e.message, 'error');
    btn.style.display = 'inline-flex';
    btnSearching.style.display = 'none';
    section.classList.remove('visible');
  }
}

// ── Clear Data ───────────────────────────────────────────
async function clearAllData() {
  if (!confirm('⚠ 确认清空所有数据？\n这将删除所有职位、评分和投递记录，不可恢复。')) return;
  try {
    await api('/api/data/clear', { method: 'POST' });
    toast('数据已清空', 'success');
    currentPage = 1;
    await refreshAll();
  } catch(e) {
    toast('清空失败: ' + e.message, 'error');
  }
}

// ── Log ──────────────────────────────────────────────────
async function loadLog() {
  const data = await api('/api/search/log');
  const box = document.getElementById('logBox');
  box.textContent = data.logs.join('\n') || '等待操作...';
  box.scrollTop = box.scrollHeight;
}

// ── Search Status ────────────────────────────────────────
async function checkSearchStatus() {
  const data = await api('/api/search/status');
  const btn = document.getElementById('btnSearch');
  const btnSearching = document.getElementById('btnSearching');
  if (data.running) {
    btn.style.display = 'none';
    btnSearching.style.display = 'inline-flex';
  } else {
    btn.style.display = 'inline-flex';
    btnSearching.style.display = 'none';
  }
  return data.running;
}

// ── Search Progress ──────────────────────────────────────
function formatElapsed(sec) {
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + 'm' + s + 's';
}

async function loadProgress() {
  try {
    const p = await api('/api/search/progress');
    const section = document.getElementById('progressSection');

    if ((p.phase === 'idle' && !p.running) || window._progressDismissed) {
      section.classList.remove('visible');
      return;
    }

    section.classList.add('visible');
    document.getElementById('progressLabel').textContent = p.phase_label || '处理中...';
    document.getElementById('progressSteps').textContent =
      p.total_steps > 0 ? `第 ${p.current_step}/${p.total_steps} 步` : '—';

    const pct = Math.min(100, p.percent || 0);
    document.getElementById('progressFill').style.width = pct + '%';

    document.getElementById('progressNew').textContent = (p.new_this_round || 0).toLocaleString();
    document.getElementById('progressTotal').textContent = (p.total_found || 0).toLocaleString();
    document.getElementById('progressElapsed').textContent = formatElapsed(p.elapsed || 0);

    // 完成/错误时 3s 后隐藏进度条
    if (p.phase === 'done' || p.phase === 'error') {
      if (p.phase === 'error') {
        document.getElementById('progressLabel').textContent = '⚠️ ' + (p.error || '搜索出错');
      }
      document.getElementById('progressFill').style.width = '100%';
      window._progressDismissed = true;
      setTimeout(() => section.classList.remove('visible'), 4000);
    }
  } catch(e) {
    // 静默忽略
  }
}

// ── Refresh All ──────────────────────────────────────────
async function refreshAll() {
  await Promise.all([loadStats(), loadJobs(currentPage), loadLog(), checkSearchStatus()]);
}

// ── Init ─────────────────────────────────────────────────
refreshAll();
setInterval(async () => {
  const running = await checkSearchStatus();
  await Promise.all([loadStats(), loadLog(), loadJobs(currentPage), loadProgress()]);
  if (!running) {
    document.getElementById('btnSearch').style.display = 'inline-flex';
    document.getElementById('btnSearching').style.display = 'none';
  }
}, POLL_INTERVAL);
</script>
</body>
</html>"""


@app.get("/")
async def index():
    """返回前端 SPA 页面（禁用浏览器缓存）。"""
    return Response(
        content=SPA_HTML,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ── API 端点 ──────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats():
    """仪表盘统计。"""
    db = get_db()
    try:
        s = db.stats()
        # 今日投递数
        cur = db._conn.cursor()
        today_start = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        cur.execute(
            "SELECT COUNT(*) FROM applications WHERE status='applied' AND applied_at >= ?",
            (today_start,),
        )
        s["today_applied"] = cur.fetchone()[0]
        return s
    finally:
        db.close()


@app.get("/api/jobs")
async def api_jobs(
    offset: int = 0,
    limit: int = 20,
    platform: str = "",
    min_score: int = 0,
):
    """职位列表。"""
    db = get_db()
    try:
        cur = db._conn.cursor()

        where = []
        params = []

        if platform:
            where.append("j.platform = ?")
            params.append(platform)

        if min_score > 0:
            where.append("s.score >= ?")
            params.append(min_score)

        where_clause = "WHERE " + " AND ".join(where) if where else ""

        # 总数
        cur.execute(f"""
            SELECT COUNT(*) FROM jobs j
            LEFT JOIN job_scores s ON s.job_id = j.id
            {where_clause}
        """, params)
        total = cur.fetchone()[0]

        # 分页数据
        cur.execute(f"""
            SELECT j.*, s.score, s.reasons, s.recommend,
                   CASE WHEN a.id IS NOT NULL THEN 1 ELSE 0 END as applied
            FROM jobs j
            LEFT JOIN job_scores s ON s.job_id = j.id
            LEFT JOIN applications a ON a.job_id = j.id
            {where_clause}
            ORDER BY COALESCE(s.score, 0) DESC, j.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])

        items = [dict(row) for row in cur.fetchall()]
        return {"total": total, "items": items, "offset": offset, "limit": limit}
    finally:
        db.close()


@app.get("/api/jobs/{job_id}")
async def api_job_detail(job_id: int):
    """单个职位详情。"""
    db = get_db()
    try:
        cur = db._conn.cursor()
        cur.execute("""
            SELECT j.*, s.score, s.reasons, s.risks, s.recommend
            FROM jobs j
            LEFT JOIN job_scores s ON s.job_id = j.id
            WHERE j.id = ?
        """, (job_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="职位不存在")
        return dict(row)
    finally:
        db.close()


@app.get("/api/applications")
async def api_applications(offset: int = 0, limit: int = 20):
    """投递历史。"""
    db = get_db()
    try:
        cur = db._conn.cursor()
        cur.execute("""
            SELECT j.title, j.company, j.salary, j.location, j.link,
                   a.status, a.applied_at, a.note
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            ORDER BY a.applied_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))
        return {"items": [dict(row) for row in cur.fetchall()]}
    finally:
        db.close()


@app.post("/api/apply/{job_id}")
async def api_apply(job_id: int):
    """对单个职位执行投递。"""
    cfg = load_config_sync()
    if not cfg.get("apply", {}).get("enabled"):
        raise HTTPException(status_code=400, detail="投递功能未启用，请设置 config.yaml apply.enabled=true")

    db = get_db()
    try:
        cur = db._conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job = cur.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="职位不存在")

        # 检查是否已投递
        cur.execute("SELECT id FROM applications WHERE job_id = ? AND status = 'applied'", (job_id,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="已投递过此职位")

        job_dict = dict(job)
        link = job_dict.get("link", "")

        # 实际投递
        cdp = CDPClient()
        try:
            await cdp.start_proxy()
            tab = await cdp.new_tab(link)
            await asyncio.sleep(2)

            applicant = Applicant(cdp, interval=0)
            success = await applicant.apply_one(job_dict)

            if success:
                db.record_application(job_id, "applied")
                add_log(f"投递成功: {job_dict.get('title', '')} @ {job_dict.get('company', '')}")
            else:
                db.record_application(job_id, "failed")
                add_log(f"投递失败: {job_dict.get('title', '')} @ {job_dict.get('company', '')}")

            await cdp.close_tab(tab)
            return {"success": success, "job_id": job_id}
        finally:
            await cdp.close()
    finally:
        db.close()


@app.post("/api/search")
async def api_search(req: SearchRequest):
    """触发搜索任务（后台执行）。"""
    if not _search_lock.acquire(blocking=False):
        raise HTTPException(status_code=400, detail="搜索任务已在运行中")

    cfg = load_config_sync()

    async def do_search():
        cdp = None
        try:
            _search_log.clear()
            reset_search_state()
            _search_state["phase"] = "connecting"
            _search_state["phase_label"] = "正在连接浏览器..."
            _search_state["start_time"] = time.time()
            add_log("🚀 搜索任务启动")

            # 解析关键词
            raw = req.keywords or ""
            if raw:
                keywords = [k.strip() for k in raw.replace("，", ",").split(",") if k.strip()]
            else:
                keywords = cfg["search"]["keywords"]
            max_pages = req.max_pages or cfg["search"]["max_pages"]

            # 预计算总步数（每个关键词 × 页数 + 1 个连接步骤）
            total_kw = len(keywords)
            _search_state["total_steps"] = total_kw * max_pages + 1
            _search_state["current_step"] = 0
            _search_state["keyword"] = ""
            _search_state["max_pages"] = max_pages
            add_log(f"关键词: {keywords}  |  最多 {max_pages} 页/词  |  共 {_search_state['total_steps']} 步")

            cdp = CDPClient()
            try:
                add_log("检查 CDP Proxy 连接...")
                if not await cdp.is_proxy_alive():
                    add_log("CDP Proxy 未运行，正在启动...")
                    _search_state["phase_label"] = "正在启动浏览器控制器..."
                    await cdp.start_proxy()
                add_log("✅ CDP Proxy 就绪")
                _search_state["current_step"] = 1
            except Exception as e:
                add_log(f"❌ CDP 连接失败: {e}")
                _search_state["phase"] = "error"
                _search_state["phase_label"] = f"连接失败: {e}"
                _search_state["error"] = str(e)
                return

            _search_state["phase"] = "searching"

            llm_cfg = cfg["llm"]
            threshold = llm_cfg.get("match_threshold", 70)

            # 加载简历
            resume_path = cfg.get("resume", {}).get("path", "resume.md")
            resume = ""
            p = PROJECT_ROOT / resume_path
            if p.exists():
                resume = p.read_text(encoding="utf-8")
            if not resume.strip():
                add_log("⚠ 简历为空，跳过 LLM 评分")
                threshold = -1

            db = Database(str(PROJECT_ROOT / cfg["database"]["path"]))
            llm = LLMClient(
                api_key=llm_cfg.get("api_key", ""),
                base_url=llm_cfg.get("base_url", ""),
                model=llm_cfg.get("model", "gpt-4o-mini"),
            ) if resume.strip() else None
            job_filter = JobFilter(llm, resume, threshold) if resume.strip() and llm else None

            try:
                urls = build_search_urls(keywords, cfg["search"].get("city", ""))
                total_new = 0
                kw_index = 0

                for entry in urls:
                    kw_index += 1
                    _search_state["keyword"] = entry["keyword"]
                    _search_state["phase_label"] = f"正在打开: {entry['keyword']}"
                    add_log(f"🔍 [{kw_index}/{total_kw}] {entry['keyword']} ({entry['platform']})")

                    try:
                        tab = await asyncio.wait_for(cdp.new_tab(entry["url"]), timeout=15)
                    except asyncio.TimeoutError:
                        add_log("  打开页面超时，跳过")
                        _search_state["current_step"] += max_pages
                        continue

                    add_log(f"  等待页面渲染...")
                    await asyncio.sleep(3)

                    for page in range(1, max_pages + 1):
                        _search_state["page"] = page
                        _search_state["phase_label"] = f"{entry['keyword']} 第{page}/{max_pages}页"
                        step = 1 + (kw_index - 1) * max_pages + page
                        _search_state["current_step"] = step
                        _search_state["elapsed"] = int(time.time() - _search_state["start_time"])

                        page_start = time.time()
                        add_log(f"  📄 第 {page}/{max_pages} 页...")

                        try:
                            jobs = await asyncio.wait_for(
                                extract_job_list(cdp, tab), timeout=15
                            )
                        except asyncio.TimeoutError:
                            add_log("  ⚠ 提取职位超时，跳过此页")
                            break
                        except Exception as e:
                            add_log(f"  ⚠ 提取异常: {e}")
                            break

                        if not isinstance(jobs, list):
                            add_log("  未获取到结构化职位数据，翻页结束")
                            break

                        if not jobs:
                            add_log("  无更多职位，翻页结束")
                            break

                        new_count = 0
                        for job in jobs:
                            link = job.get("link", "")
                            if not link:
                                continue
                            if db.job_exists(link):
                                continue
                            job_id = db.insert_job(job, entry["keyword"])
                            if job_id:
                                job["_db_id"] = job_id
                                new_count += 1

                        elapsed = time.time() - page_start
                        total_new += new_count
                        _search_state["total_found"] = total_new
                        _search_state["new_this_round"] = new_count
                        _search_state["elapsed"] = int(time.time() - _search_state["start_time"])
                        add_log(f"  ✅ 新增 {new_count} 个职位 (耗时 {elapsed:.1f}s) | 本轮累计 {total_new}")

                        if new_count == 0 and page > 1:
                            add_log("  连续空页，停止翻页")
                            break

                        # LLM 评分
                        if job_filter and new_count > 0:
                            _search_state["phase"] = "scoring"
                            _search_state["phase_label"] = f"AI 正在评估 {new_count} 个新职位..."
                            new_with_id = [j for j in jobs if j.get("_db_id")]
                            if new_with_id:
                                try:
                                    results = await asyncio.wait_for(
                                        job_filter.batch_match(new_with_id), timeout=90
                                    )
                                    matched = 0
                                    for mr in results:
                                        jid = mr["job"].get("_db_id")
                                        if jid:
                                            db.insert_score(jid, mr)
                                        if mr.get("recommend"):
                                            matched += 1
                                    add_log(f"  🤖 LLM 评分完成: {matched} 个推荐")
                                except asyncio.TimeoutError:
                                    add_log("  ⚠ LLM 评分超时，跳过评分")
                                except Exception as e:
                                    add_log(f"  ⚠ LLM 评分异常: {e}")
                            _search_state["phase"] = "searching"

                        # 翻页
                        if page >= max_pages:
                            break
                        try:
                            next_btns = await asyncio.wait_for(
                                cdp.eval(tab, 'document.querySelector(".pagination .btn-next") ? 1 : 0'),
                                timeout=8,
                            )
                            if next_btns == 1:
                                await cdp.click(tab, ".pagination .btn-next")
                                await asyncio.sleep(1.5 + random.uniform(0.5, 1.5))
                            else:
                                add_log("  无下一页")
                                break
                        except Exception:
                            add_log("  翻页失败")
                            break

                    await cdp.close_tab(tab)

                _search_state["phase"] = "done"
                _search_state["phase_label"] = "搜索完成"
                _search_state["elapsed"] = int(time.time() - _search_state["start_time"])
                s = db.stats()
                add_log(f"✅ 搜索完成! 总职位 {s['total_jobs']}, 候选 {s['candidates']} | 耗时 {_search_state['elapsed']}s")
            finally:
                db.close()
        except Exception as e:
            _search_state["phase"] = "error"
            _search_state["phase_label"] = f"异常: {str(e)[:50]}"
            _search_state["error"] = str(e)
            add_log(f"❌ 搜索异常: {e}")
            import traceback
            add_log(traceback.format_exc()[-200:])
        finally:
            if cdp is not None:
                try:
                    await cdp.close()
                except Exception:
                    pass
            _search_lock.release()
            if _search_state["phase"] not in ("done", "error"):
                _search_state["phase"] = "done"
            add_log("🔓 搜索锁已释放")

    asyncio.create_task(do_search())
    return {"message": "搜索已启动"}


@app.get("/api/search/status")
async def api_search_status():
    """查询搜索任务是否在运行。"""
    return {"running": _search_lock.locked()}


@app.get("/api/search/log")
async def api_search_log():
    """获取搜索日志。"""
    return {"logs": list(_search_log)}


@app.get("/api/search/progress")
async def api_search_progress():
    """获取搜索进度详情。"""
    elapsed = 0
    if _search_state.get("start_time"):
        elapsed = int(time.time() - _search_state["start_time"])
    return {
        **{k: v for k, v in _search_state.items() if k != "start_time"},
        "elapsed": elapsed,
        "running": _search_lock.locked(),
        "percent": min(99, int(_search_state["current_step"] / max(1, _search_state["total_steps"]) * 100)) if _search_state["total_steps"] > 0 else 0,
    }


@app.post("/api/data/clear")
async def api_clear_data():
    """清空所有职位、评分和投递记录（搜索进行中禁止操作）。"""
    if _search_lock.locked():
        raise HTTPException(status_code=409, detail="搜索正在进行中，请等待完成后再清空")
    db = get_db()
    try:
        cur = db._conn.cursor()
        cur.execute("DELETE FROM applications")
        cur.execute("DELETE FROM job_scores")
        cur.execute("DELETE FROM jobs")
        db._conn.commit()
        _search_log.clear()
        reset_search_state()
        return {"message": "数据已清空"}
    finally:
        db.close()


# ── 入口 ──────────────────────────────────────────────────

def start_server(host: str = "127.0.0.1", port: int = 8800):
    import uvicorn
    print(f"\n  🌐 offeragent Web 界面:  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
