# offeragent — 智能招聘智能体

自动搜索、筛选、投递职位。Python 决策层 + CDP Proxy 操控 Chrome 浏览器。

## 协作者快速上手（5 步）

### 第 1 步：克隆项目

```bash
git clone https://github.com/steve-joking/offeragent.git
cd offeragent
```

### 第 2 步：环境要求

| 工具 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 主程序 |
| Node.js | 18+ | CDP Proxy（浏览器控制） |
| Chrome | 最新版 | 被控浏览器 |

### 第 3 步：安装依赖

```bash
# Python 依赖
pip install -e .

# Node.js 无需额外安装 npm 包（CDP Proxy 使用 Node.js 内置模块）
```

### 第 4 步：配置

```bash
# 复制环境变量模板
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key：
# DEEPSEEK_API_KEY=sk-xxxxx
```

编辑 `config.yaml`：
- `llm.api_key` 留空即可（会自动读 `.env` 或环境变量）
- `search.keywords` 可在 Web 界面输入，无需改此文件

编辑 `resume.md`：
- 填写你的真实简历，LLM 会以此与 JD 匹配打分

### 第 5 步：启动

**方式一：Web 界面（推荐）**

```bash
# 先开启 Chrome 远程调试（macOS 示例）：
# /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

python -m src.web start
# 打开 http://127.0.0.1:8800
```

**方式二：命令行**

```bash
# 搜索 + 抓取 + LLM 筛选 + 入库
python -m src.main search

# 查看统计
python -m src.main stats

# 投递（需先在 config.yaml 开启 apply.enabled: true）
python -m src.main apply
```

## 架构

```
Python 智能体
├── src/web.py          Web 界面入口（FastAPI + SPA）
├── src/cdp_client.py   CDP Proxy HTTP 客户端
├── src/searcher.py     搜索模块：构造搜索 URL
├── src/scraper.py      抓取模块：DOM 提取职位数据
├── src/filter.py       筛选引擎：LLM 解析 JD + 简历匹配
├── src/applicant.py    投递模块：点击投递 + 处理弹窗
├── src/db.py           SQLite 持久化
├── src/llm.py          LLM 调用封装（支持 DeepSeek / OpenAI 兼容接口）
│
├── scripts/cdp-proxy/         CDP Proxy (Node.js，零依赖)
│   ├── check-deps.mjs         启动检测
│   ├── cdp-proxy.mjs          HTTP → CDP 网关
│   └── browser-discovery.mjs  浏览器发现
│
├── config.yaml        配置文件
├── resume.md          简历
└── data/             数据库 & 日志（自动生成，已 gitignore）
```

## 扩展开发

所有模块独立，方便扩展：

- **新增招聘平台**：在 `searcher.py` 添加平台 URL 模板，`scraper.py` 添加对应 CSS 选择器
- **优化筛选逻辑**：修改 `filter.py` 中的 prompt 模板
- **新增 LLM 提供商**：`llm.py` 使用 OpenAI 兼容接口，直接换 `base_url` 即可
- **投递流程定制**：修改 `applicant.py` 中的按钮选择器和确认流程
