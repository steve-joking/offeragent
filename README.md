# offeragent — 智能招聘智能体

自动搜索、筛选、投递职位。Python 决策层 + CDP Proxy 操控 Chrome 浏览器。

## 快速开始

### 1. 环境准备

- **Python 3.11+**
- **Node.js 22+**（CDP Proxy 依赖）
- **Chrome 浏览器**（并开启远程调试）

开启远程调试：Chrome 地址栏输入 `chrome://inspect/#remote-debugging`，勾选 "Allow remote debugging"。

### 2. 安装依赖

```bash
cd offeragent
pip install -e .
```

### 3. 配置

编辑 `config.yaml`：
- 填写 LLM API Key（或设置环境变量 `OPENAI_API_KEY`）
- 调整搜索关键词、城市、薪资范围

编辑 `resume.md`：
- 填写你的真实简历，LLM 会以此与 JD 匹配

### 4. 运行

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
├── src/main.py       入口：CLI，编排主流程
├── src/cdp_client.py CDP Proxy HTTP 客户端
├── src/searcher.py   搜索模块：构造搜索 URL
├── src/scraper.py    抓取模块：DOM 提取职位数据
├── src/filter.py     筛选引擎：LLM 解析 JD + 简历匹配
├── src/applicant.py  投递模块：点击投递 + 处理弹窗
├── src/db.py         SQLite 持久化
├── src/llm.py        LLM 调用封装
│
├── scripts/cdp-proxy/         CDP Proxy (Node.js)
│   ├── check-deps.mjs         启动检测
│   ├── cdp-proxy.mjs          HTTP → CDP 网关
│   └── browser-discovery.mjs  浏览器发现
│
├── config.yaml        配置文件
├── resume.md          简历
└── data/offeragent.db 数据库（自动生成）
```

## 扩展开发

所有模块独立，方便扩展：

- **新增招聘平台**：在 `searcher.py` 添加平台 URL 模板，`scraper.py` 添加对应 CSS 选择器
- **优化筛选逻辑**：修改 `filter.py` 中的 prompt 模板
- **新增 LLM 提供商**：`llm.py` 使用 OpenAI 兼容接口，直接换 `base_url` 即可
- **投递流程定制**：修改 `applicant.py` 中的按钮选择器和确认流程
