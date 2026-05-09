# PaperPrism
<img width="1024" height="314" alt="1777376072343image" src="https://github.com/user-attachments/assets/1da69e2d-9a92-45e2-995e-eb2f100e88b6" />

<p align="center">
  <a href="README.md">English</a> | 中文
</p>

<p align="center">
  <a href="https://pypi.org/project/paperprism-agent/"><img src="https://img.shields.io/pypi/v/paperprism-agent.svg?label=PyPI&color=2b6cb0" alt="PyPI 版本"></a>
  <a href="https://pypi.org/project/paperprism-agent/"><img src="https://img.shields.io/pypi/pyversions/paperprism-agent.svg?label=python" alt="Python 版本"></a>
  <a href="https://github.com/MrMao007/PaperPrism/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-brightgreen.svg" alt="许可证: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/manifest-MV3-f59e0b.svg" alt="Chrome MV3">
  <img src="https://img.shields.io/badge/本地优先-yes-7c3aed.svg" alt="本地优先">
  <a href="https://github.com/MrMao007/PaperPrism/actions/workflows/release.yml"><img src="https://github.com/MrMao007/PaperPrism/actions/workflows/release.yml/badge.svg?branch=main" alt="Release CI"></a>
  <a href="https://github.com/MrMao007/PaperPrism/releases/latest"><img src="https://img.shields.io/github/v/release/MrMao007/PaperPrism?display_name=tag&sort=semver" alt="最新发布"></a>
  <a href="https://github.com/MrMao007/PaperPrism/stargazers"><img src="https://img.shields.io/github/stars/MrMao007/PaperPrism?style=social" alt="GitHub Stars"></a>
</p>

本地优先、隐私保护的 arxiv 论文管理工具。Chrome 扩展监测你的 arxiv 下载行为，本地 Agent 自动将每篇论文归档到隐藏工作区，提取元数据，并通过你选择的 LLM 进行分类。**论文数据始终留在你自己的机器上，不会上传到任何云端。**

- **Chrome 扩展** — 常驻浏览器工具栏的全功能研究助手。一键归档任意 arxiv PDF；内置 Dashboard 让你无需离开 Chrome 即可浏览、筛选和管理整个文献库：
  - 📋 **Dashboard** — 在专用浏览器标签页中浏览 / 筛选 / 查看 PDF / 打标签 / 删除论文。
  - 🤖 **LLM TL;DR** — 每次入库时自动生成背景 / 方法 / 关键结论三段式摘要，无需额外操作。
  - 🏷️ **内联标签编辑** — 按 Enter 添加标签，点 × 删除；编辑结果即时同步到本地 Agent。
  - 🔍 **三路搜索** — FTS5 全文检索、标签名匹配、标题/摘要回退，一个搜索框全搞定。
  - 📰 **每周研究报告** — 侧边栏展示过去七天内你阅读和归档的论文摘要。
  - 📂 **批量导入** — 选择一个现有 PDF 文件夹；每个文件的处理进度由 Agent 实时推送。
  - ⚙️ **首次运行向导** — Options 页面引导你选择 LLM 提供商并填入 API Key；Dashboard 中随时可一键进入**设置**。
  <p align="center">
  <img width="344" height="170" alt="6851B0EF-696D-4B07-BFC4-B343B72C12C3" src="https://github.com/user-attachments/assets/c6cf3929-06e4-48cd-be2e-9df1fb8f4ec5" />
  </p>
- **本地 Agent** — 完全运行在你机器上的轻量 FastAPI 服务（`http://127.0.0.1:17321`），负责从原始 PDF 到结构化、LLM 富化、可检索记录的完整数据流水线：
  - 🗄️ **SQLite + FTS5** — 所有论文、标签、事件和话题均本地存储，无云端数据库，无同步服务。
  - 🧠 **多 LLM 提供商** — 支持 OpenAI、Anthropic、Google Gemini、通义千问、DeepSeek、Moonshot、OpenRouter 和 Ollama；随时切换提供商，数据分毫不损。
  - 🔎 **arxiv ID 解析器** — 两步流水线（文件名解析 → LLM 兜底），即使是改过名的历史 PDF 也能找回 arxiv ID。
  - 🏷️ **入库自动打标** — 每篇新论文自动生成 2–5 个简短 LLM 标签。
  - 📝 **入库即生成 TL;DR** — 入库完成前即写入 2–3 句摘要（背景 / 方法 / 关键结论）。
  - 📄 **PDF 全文送入 LLM** — 提取正文喂给大模型，分类质量远超仅凭标题和摘要。
  - 🚀 **登录自启动** — macOS LaunchAgent 让服务常驻后台；`paperprism-agent install` 一键配置。
  <p align="center">
  <img width="1728" height="959" alt="5134446E-64C5-43BA-B1B3-E069EE994F1F" src="https://github.com/user-attachments/assets/285fbcc3-1e9e-407d-8350-8184ba62e7eb" />
  </p>
- **标签与话题** — 构建在文献库之上的轻量知识图谱层，将一堆 PDF 变成结构化、可浏览的研究地图：
  - 🏷️ **自动标签** — 每次入库时 LLM 生成 2–5 个简短标签，立即出现在 Dashboard，无需用户操作。
  - ✏️ **用户标签** — 随时内联添加或删除标签，变更实时持久化到 SQLite。
  - 🗂️ **话题卡片** — 选中任意一批论文，合成为一个命名话题，包含一句话摘要和所有论文的并集标签；非常适合跨多篇论文追踪某条研究脉络。
  - 💾 **完全本地** — 标签和话题与论文存储在同一个 SQLite 数据库中，不依赖任何外部服务。
  <p align="center">
  <img width="1728" height="959" alt="7931BD61-E996-46B3-8C26-00D7BAC5D687" src="https://github.com/user-attachments/assets/c3681265-979a-4c95-b0db-211b51ea93e6" />
  </p>
- **Atlas（星图）** — 整个文献库的交互式二维语义地图。每篇论文被编码为 384 维 embedding（`BAAI/bge-small-en-v1.5`，约 130 MB，首次使用时自动下载），再通过 UMAP 投影到二维平面。画布包含四个图层：
  - ⭐ **你的星辰** — 已归档的论文按语义相似度分布，聚类模式一眼揭示你的研究版图。
  - 🔴 **阅读轨迹** — 连接过去 30 天活动（归档事件）的时序轨迹，清晰呈现你的研究重心如何迁移。
  - 🔵 **远方星辰** — 当天的 arXiv 推送论文（最多 200 篇）投影到同一空间；悬停预览标题和摘要，点击**加入文献库**可直接从地图归档。
  - 🟣 **星云** — 最多 5 篇盲点推荐：处于你文献库活跃区域周边稀疏位置的推送论文，帮你发现尚未探索的相邻领域。

  点击任意点，侧边抽屉将展示标题、摘要和直达链接。使用 ⚙️ 设置面板选择哪些 arXiv 分类为「远方星辰」图层提供数据。UMAP 投影结果在服务端缓存，重复轮询几乎即时响应。
  <p align="center">
  <img width="1728" height="959" alt="550E4506-27C1-43EF-99AC-8835DFC6696B" src="https://github.com/user-attachments/assets/511c219d-4777-4305-8190-43d3ba8f7d3c" />
  </p>

## 快速开始（两步搞定）

最快体验 PaperPrism 的方式，无需 clone 仓库，无需手动构建。

### 1. 安装 Chrome 扩展

[![从 Chrome 应用商店安装](https://img.shields.io/chrome-web-store/v/jjlclcocagjnohgcpbgcpkodcnmmabif?label=Chrome%20%E5%BA%94%E7%94%A8%E5%95%86%E5%BA%97&color=4285F4&logo=googlechrome&logoColor=white)](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)
[![用户数](https://img.shields.io/chrome-web-store/users/jjlclcocagjnohgcpbgcpkodcnmmabif?label=用户数&color=4285F4)](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)
[![评分](https://img.shields.io/chrome-web-store/rating/jjlclcocagjnohgcpbgcpkodcnmmabif?color=4285F4)](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)

打开 [Chrome 应用商店中的 PaperPrism 页面](https://chromewebstore.google.com/detail/jjlclcocagjnohgcpbgcpkodcnmmabif)，点击**添加到 Chrome**。

### 2. 用 `uvx` 启动本地 Agent

```bash
uvx paperprism-agent serve
```

就这样。点击工具栏中的 PaperPrism 图标 — 弹出窗口应显示 **Agent: online**，四步首次运行向导将帮你选择 LLM 提供商并填入 API Key。之后下载任意 arxiv PDF，PaperPrism 将在数秒内完成归档 + 分类 + 自动打标。

> **第一篇论文会稍慢一些。** 首次入库时，Agent 会从 HuggingFace 下载 embedding 模型（[BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)，约 130 MB，仅此一次），缓存到 `~/.cache/huggingface/hub/`。你会看到类似 `Downloading embedding model … (~130 MB, one-time)` 的日志。此后启动完全离线，从本地缓存加载。

前置条件：[`uv`](https://docs.astral.sh/uv/) 0.4+（macOS / Linux 安装命令：`curl -LsSf https://astral.sh/uv/install.sh | sh`）。

想让 Agent 随登录自启动（macOS）并在崩溃后自动恢复？请使用下方[推荐的 `uv tool install` 方式](#-推荐--uv-tool-install任意系统)，而非 `uvx`。

## 安装

### ⭐ 推荐 — `uv tool install`（任意系统）

这是**主要的、官方支持的安装方式**，适用于 macOS（Apple Silicon 和 Intel）、Linux 和 WSL。无需预编译二进制文件，无需处理签名弹窗，无需 Rosetta — 只需从 PyPI 拉取一个 Python 包，所有内容（SQL 迁移、默认配置）均已打包。

前置条件：[uv](https://docs.astral.sh/uv/) 0.4+。

```bash
# 如果尚未安装 uv（https://docs.astral.sh/uv/#installation）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装 PaperPrism Agent（在 ~/.local/bin/paperprism-agent 创建持久化入口）
uv tool install paperprism-agent

# 验证
paperprism-agent version        # -> 0.2.0

# macOS：注册 LaunchAgent，实现登录自启动 + 自动重启
paperprism-agent install
paperprism-agent status         # -> state = running

# Linux / WSL：在前台运行（systemd 用户单元计划在 v0.2 支持）
paperprism-agent serve
```

随时使用 `uv tool upgrade paperprism-agent` 升级，然后 `paperprism-agent restart` 重启。

#### 用 `uvx` 一次性体验（无需安装）

```bash
uvx paperprism-agent serve
```

适合在临时环境中快速体验。**不要**与 `paperprism-agent install` 混用 — `install` 子命令会拒绝将 LaunchAgent 注册到 `uvx` 的临时缓存路径（该路径随时可能被垃圾回收）。如需永久安装请使用上面的 `uv tool install`。

### 其他安装方式

仅在不想在机器上安装 `uv` 时使用以下方式。

<details>
<summary><strong>macOS — <code>.pkg</code> 安装包 / 一键 shell 脚本</strong></summary>

```bash
# 一键安装（从 GitHub Releases 下载最新二进制到 ~/.local/bin
# 并注册 LaunchAgent；可通过 PAPERPRISM_PREFIX=... 覆盖安装路径）：
curl -fsSL https://raw.githubusercontent.com/MrMao007/PaperPrism/main/packaging/install.sh | bash
```

或从 [Releases 页面](https://github.com/MrMao007/PaperPrism/releases)下载 `paperprism-agent-<version>-macos-arm64.pkg` 双击安装。安装程序会将二进制放在 `/usr/local` 下，并自动为你的账号注册 LaunchAgent。

Intel Mac（2020 年前）：我们不再提供原生 x86_64 二进制（GitHub 的 Intel CI runner 已退役）。Intel Mac 可通过 Rosetta 2 运行 arm64 `.pkg`（`softwareupdate --install-rosetta`），或者 — 更简单 — 直接使用上方**推荐的 `uv tool install` 方式**，在 Intel 上完全原生运行。

</details>

<details>
<summary><strong>macOS / Linux — Homebrew</strong></summary>

```bash
brew tap MrMao007/paperprism
brew install paperprism-agent
brew services start paperprism-agent
```

</details>

<details>
<summary><strong>Linux — 一键 shell 脚本</strong></summary>

```bash
curl -fsSL https://raw.githubusercontent.com/MrMao007/PaperPrism/main/packaging/install.sh | bash
```

Linux 上的自启动暂未集成 — 手动运行 `paperprism-agent serve` 或自行配置 systemd 用户单元。

</details>

<details>
<summary><strong>Windows / Debian — 从源码安装</strong></summary>

`.msi` 和 `.deb` 计划在 v0.2 支持。目前请使用 `uv tool install paperprism-agent`（推荐），或从 git 安装：

```bash
pip install git+https://github.com/MrMao007/PaperPrism#subdirectory=agent
paperprism-agent serve
```

</details>

## 从源码运行

无需下载任何发布产物即可体验 PaperPrism 的最快方式：clone 仓库，一次性安装，将未打包的扩展加载到 Chrome。支持 macOS、Linux 和 WSL，总配置时间约 3 分钟。

### 前置条件

| 工具 | 版本 | 说明 |
|---|---|---|
| Python | **>= 3.10** | `hatchling` 不支持旧版解释器。通过 `python3 --version` 确认。 |
| Node.js | **>= 18** | 用于 WXT 构建。通过 `node --version` 确认。 |
| Google Chrome | 任意近期版本 | 或任何基于 Chromium 的浏览器（Edge、Brave、Arc）。 |
| Git | 任意 | |

缺少 Python 3.10+？macOS：`brew install python@3.11`；Ubuntu：`sudo apt install python3.11 python3.11-venv`。

### 1. Clone 仓库

```bash
git clone https://github.com/MrMao007/PaperPrism.git
cd PaperPrism
```

### 2. 安装并启动 Agent

```bash
cd agent
python3.11 -m venv .venv            # 也可用 python3.10/3.12
source .venv/bin/activate
pip install -e .

# 验证 CLI 可用
paperprism-agent version

# macOS：注册 LaunchAgent，实现登录自启动
paperprism-agent install
paperprism-agent status              # 应显示 "state = running"

# Linux / WSL：无 launchd，在另一个终端中运行
# paperprism-agent serve
```

在另一个 shell 中进行健康检查：

```bash
curl http://127.0.0.1:17321/api/health
# {"ok":true,"version":"0.2.0",...}
```

所有状态存储在 `~/.paperprism/` 下（vault、SQLite 数据库、日志、密钥）。

### 3. 构建并加载 Chrome 扩展

```bash
cd ../extension
npm install                          # 安装 WXT + React 工具链
npm run build                        # 输出到 .output/chrome-mv3/
```

然后将其加载到 Chrome：

1. 打开 **`chrome://extensions`**
2. 右上角开启**开发者模式**
3. 点击**加载已解压的扩展程序**
4. 选择文件夹 `extension/.output/chrome-mv3`

工具栏出现 **PaperPrism** 图标，建议固定以便随时使用。

> 想在编辑扩展时热重载？用 `npm run dev` 代替 `npm run build` — WXT 会在保存时自动重建；提示时在 `chrome://extensions` 页面点击**更新**即可。

### 4. 完成首次运行向导

1. 点击工具栏图标 → 弹出窗口应显示 **Agent: online**。
2. 点击**设置**（底部）— Options 页面打开并自动启动四步向导：
   - **第 1 步** 探测 Agent 连接状态。
   - **第 2 步** 选择 LLM 提供商（通义千问 / OpenAI / Anthropic / Google Gemini / DeepSeek / Moonshot / OpenRouter / Ollama 本地）。API base 和环境变量自动填充。
   - **第 3 步** 粘贴你的 API Key（Ollama 跳过此步）。密钥写入 `~/.paperprism/secrets.env`（权限 600），立即注入 Agent 进程；向导随后发送一个小型对话请求以验证密钥有效。
   - **第 4 步** 点击**打开 Dashboard**。

### 5. 归档你的第一篇论文

打开任意 arxiv 摘要页，例如 <https://arxiv.org/abs/2310.06825>，点击 **Download PDF**（或 PaperPrism 弹出窗口中的**归档当前标签页**）。Agent 会摄取 PDF、提取元数据、使用 LLM 分类，并**自动打上 2–5 个标签**，Dashboard 在数秒内即可看到这篇论文。入库自动打标可在 Options 页面 → LLM 部分开关。

### 6. （可选）批量导入现有 PDF 文件夹

手头有多年积累的 arxiv 论文本地存档？打开 Dashboard，点击**导入文件夹**，选择任意目录。扩展遍历目录树，将所有 `.pdf` 上传给 Agent，并实时推送进度（已导入 / 重复 / 失败计数以及最后几条错误信息）。中途可取消。

每个 PDF 的 arxiv ID 解析分两步：

1. **文件名优先** — 尝试将文件名（如 `2504.19413v1.pdf`、`Attention_1706.03762.pdf`）作为候选，并在 arxiv API 上验证。
2. **LLM 兜底** — 若第 1 步未命中，将 PDF 第一页送给你配置的 LLM，要求以严格的 `{"arxiv_id": ...}` 格式返回，再在 arxiv 上验证。

两步均失败时，论文仍以合成的 `local-<sha>` ID 归档，文件不会丢失。

### 7. （可选）将论文汇总为话题

在 Dashboard 中勾选任意数量的论文，在批量工具栏中点击**自动打标所选**。Agent 批量将其送给 LLM，持久化每篇论文的标签，最后将这批论文汇总为一个**话题**卡片（名称 + 1–3 句摘要 + 所有共同标签）。从顶部导航切换到**话题**标签页可浏览所有话题；点击进入详情页查看完整论文列表。话题删除后，单篇论文的标签始终保留。

### 开发时常用命令

```bash
# Agent
paperprism-agent status               # launchd 状态
paperprism-agent logs --follow        # 追踪 stdout/stderr
paperprism-agent restart              # 强制 launchd 重新执行
paperprism-agent uninstall            # 移除 LaunchAgent

# 扩展
cd extension
npm run dev                           # 监听模式，自动重建
npm run build                         # 一次性生产构建
npm run compile                       # 仅类型检查，不输出文件
```

### 常见问题排查

| 现象 | 解决方案 |
|---|---|
| `pip install -e .` 报 `hatchling>=1.25` 错误 | 你的 venv 是用 Python < 3.10 创建的。用 `python3.11 -m venv .venv` 重新创建。 |
| 弹出窗口显示 **Agent: offline** | 运行 `paperprism-agent status`；若未运行，执行 `paperprism-agent restart`。检查端口 17321 是否被占用：`lsof -i :17321`。 |
| `npm install` 报类型错误 | 运行一次后重试 — WXT 在 `postinstall` 时生成 `.wxt/tsconfig.json`。 |
| 向导**保存并测试**失败 | 查看 `~/.paperprism/logs/agent.log`；LLM 错误（401 / 404 / 超时）通常一目了然。 |
| 修改了 `agent/` 的代码但 Agent 仍运行旧版本 | `paperprism-agent restart` — `pip install -e .` 只是链接源码，已加载的进程仍持有旧的导入。 |
| Agent 启动失败，报 `Form data requires "python-multipart"`（或其他 `ModuleNotFoundError`） | 你在 `agent/pyproject.toml` 的 `[project].dependencies` 中添加了新依赖但没有重新同步 venv。在已激活的 venv 中执行 `pip install -e .`（可编辑安装只链接源码，不会自动安装新声明的依赖）。 |
| Chrome 提示"此扩展程序可能很快不再受支持" | 你误加载了 MV2 产物；确保加载的是 `.output/chrome-mv3/`，而非任何旧版压缩包。 |

## 本地构建发布包

```bash
# 1. 生成单文件二进制
bash packaging/pyinstaller/build.sh
# -> packaging/pyinstaller/dist/paperprism-agent

# 2. （仅 macOS）打包为 .pkg
bash packaging/macos/build_pkg.sh 0.2.0
# -> packaging/macos/dist/paperprism-agent-0.2.0-macos-<arch>.pkg
```

[`.github/workflows/release.yml`](.github/workflows/release.yml) 中的 CI 工作流在推送 `v*` 标签时，会在 macOS（仅 arm64）和 Linux（arm64 + x86_64）上执行相同构建，并将压缩包和 `.pkg` 自动附加到 GitHub Release。（Intel macOS 原生构建已停止，因为 GitHub 的 `macos-13` runner 池正在退役；Intel Mac 通过 Rosetta 2 运行 arm64 二进制。）

## 项目结构

```
agent/           FastAPI + SQLite Agent（Python 3.10+）
extension/       Chrome MV3 扩展（WXT + React + TS）
packaging/       install.sh、PyInstaller spec、macOS .pkg、Homebrew formula
.github/         发布自动化
```

## 许可证

Apache-2.0 — 详见 [LICENSE](LICENSE)。
