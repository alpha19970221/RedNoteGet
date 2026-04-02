# 红薯市调智能体 (RedNote Market Intelligence Agent)

这是一个基于 FastAPI、LangGraph 和大语言模型（LLM）构建的全栈市场需求分析自动化智能体。通过对接底层数据采集引擎，智能体能够根据用户输入的搜索关键词，全自动收集社交媒体上的爆款讨论、精准抓取高价值评论，并通过精细的 Token 控制与多步 AI 总结机制，输出一份高质量的 Markdown 格式市场洞察报告。

**主要使用场景**：产品经理竞品分析、跨境电商选品、用户真实痛点挖掘、市场情绪分析等。

---

## 🌟 核心特性

- **✨ 响应式 Web 界面**：深色模式的现代 UI，实时展示智能体六阶段的 SSE 事件流推进，提供极佳的交互体验。
- **🧠 智能评论处理引擎**：抛弃了传统的“爬取全部数据”的做法，在提取阶段即通过严格的正则策略剔除水军、纯数字串以及无意义的符号，并按字数降序进行高信息密度提取。
- **🛡️ 零超限（Zero-TPM Limit）分析架构**：针对模型 Token 限制进行了底层架构解耦。智能体会对提取到的每一篇长贴进行**独立的 AI 初步摘要提炼**（`PostSummarizer`），再将各篇精炼摘要汇总至 `NeedsAnalyzer` 处理，彻底杜绝输入超载。
- **⚡️ 纯内存处理，告别本地 I/O 阻塞**：跳过了多媒体资源下载，大幅提升数据获取与报告产出速度。
- **📝 从零到一自动撰写报告**：自动分析得出受众画像、痛点场景以及推荐的产品建议，并在本地硬盘 `reports/` 下持久保存 Markdown。

---

## 🛠 安装与配置

本项目运行需要基于 Python 与 Node.js 两种环境。核心智能体由 Python 驱动，反爬签名破解借助了 Node.js（`Spider_XHS-master` 模块）。

### 1. 环境准备
- **Python >= 3.12**
- **Node.js** (推荐 >= 16)

### 2. 克隆与依赖安装
```bash
git clone https://github.com/alpha19970221/RedNoteGet.git
cd RedNoteGet

# 安装 Python 后端库
pip install -r requirements.txt

# 安装 Spider_XHS 接口防反爬依赖包
cd Spider_XHS-master
npm install
cd ..
```

### 3. 配置环境变量
复制本项目根目录下的示例配置，并填入您的 API 密钥：
```bash
cp .env.example .env
```
在 `.env` 中修改 `OPENAI_API_KEY`（目前默认使用 `gpt-4o` 模型进行数据解析）。

### 4. 获取账户 Cookie
为了正常调用数据采集接口，您需要一个已登录的 Cookie 字符串：
1. 使用浏览器（无痕模式更佳）访问小红书网页版并登录。
2. 打开开发者工具（`F12`）切换到 `Network`，随便点击一篇帖子，在抓包记录中找到任意请求的 Request Headers 中的 `Cookie` 字段。
3. 您可以在打开 Web 网页后直接在设置面板输入该 Cookie，或者将其存在 `.env` 中。

---

## 🚀 启动指引

### 方式一：Web 可视化模式（推荐）

直接启动 FastAPI 服务器：
```bash
python server.py
# 或使用 uvicorn: uvicorn server:app --host 0.0.0.0 --port 8000
```
在浏览器中访问 [http://localhost:8000/](http://localhost:8000/)：
- 左侧边栏包含**历史调研报告归档**，支持再次阅览。
- 右上角 `⚙️ 配置` 提供修改 Cookie 的入口。
- 您可以自行调节 `目标拉取篇数`（不建议大于 15 篇，以免触发过多反爬机制）。

### 方式二：命令行 CLI 模式

若您只想在终端安静地拉取，可以使用 CLI 入口。
```bash
python -m agent.main_agent --query "降噪耳机"
```
完成后终端会直接输出报告日志，同样，报告也会被归档至 `reports/` 目录下。

---

## 🧩 智能体工作流 (Agent Workflow)

智能体底层基于 LangGraph 的有限状态机运行：

1. **`🔍 搜索准备` (KeywordGenerator)**：确定搜索意图。
2. **`📊 热帖搜索` (PostSearcher)**：爬取含一定点赞量的高热度相关笔记。
3. **`💬 评论区抓取` (CommentExtractor)**：读取文章正文并通过去重、去噪提取出最具价值的高纯度评论。
4. **`🤖 AI 内容总结` (PostSummarizer)**：对当前分析文章与百余条评论进行独立的精压缩，转出高密度的信息摘要（防长文本撑爆 Token）。
5. **`🧠 需求分析` (NeedsAnalyzer)**：汇总所有上述提炼出的各篇文章摘要，做全局整合与横向对比。
6. **`📝 报告生成` (ReportGenerator)**：由“资深商业分析师”的人设起草专业且易于阅读的市场商业挖掘报告。

---

## ⚠️ 免责声明

该项目的诞生仅用于学习 AI 大语言模型架构及 Agent 开发范式。数据抓取模块来源于开源社区的无头爬虫方案，一切产生的后果及责任均由滥用者承担。请不要高并发发起请求，请尊重和爱护平台服务器负载。
