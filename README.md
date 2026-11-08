# CicaNest

自托管的多平台 AI bot 应用，代码和命令里叫 nest。一个后台进程管多个 bot，每个 bot 有自己的账号、名字、头像、人设和模型。主要接 QQ，也能接 Discord 和企业微信。

> **风险**：QQ 是通过 NapCat 这类程序用个人号接入的，不是腾讯官方的机器人接口，违反 QQ 的用户协议。号可能被提示「风险设备」、限制功能甚至冻结。请用专门的小号，不要用主号。

## 能做什么

- **群聊**：判断什么时候该接话（被点名必回、偶尔插话、几个 bot 轮流、冷却和限额），一次回复可以像人一样分两三条发；能看到引用和回复的上下文。
- **答题**：被点名问正经问题时认真回答。先分辨是闲聊、正经问题、问以前的聊天，还是问某个项目本身：正经问题可以联网查，问以前的聊天时翻存下的记录。
- **项目资料**：一个群可以绑定一个项目的帮助资料，问项目的问题照资料回答，答不上的转给维护资料的人。
- **表情包和黑话**：收集群里的表情包，由看图模型写描述，回复时可以带一张；从聊天里学群里的黑话，用到时加进提示词。
- **私聊**：只回加过的人；聊久了自动把早先的对话记成摘要。
- **监控**：定时检查订阅（RSS）、网页变化和价格，有变化就让 bot 按人设改写后推送到群里或私聊。
- **控制台**：网页版，可以远程访问（局域网和 Tailscale，密码登录）。看实时消息和每条消息的判断理由；改 bot 的人设、模型、备用模型和每个群的开关；看用量；管理表情包、黑话、监控、项目资料、QQ 容器、存储清理和备份。
- **试运行**：每个 bot、每个群都可以先「只看不说」，只记录本来会发的消息。

## 项目资料怎么检索

资料是 Markdown，每节固定写标题、别名、界面位置、平台、版本、报错原文。一道项目问题的处理：

1. **截图读字**：提问里的截图先由看图模型抄出上面的字，像密钥的长串打码。
2. **三路找候选**：关键词 BM25（中文按相邻两字切词，字段加权）、向量检索、报错原文逐字匹配，按名次融合（RRF）。
3. **重排**：本机的重排模型给候选打分，最高分太低就算「资料里没有」，这道题转给维护资料的人。
4. **模型挑资料**：回答问题的 bot 自己的模型，从排好的候选（每节只给标题和摘要）里挑出真能回答的几节。出错时退回重排的结果。
5. **照资料作答**：只照这几节回答，界面路径照资料原样说，资料没写的直说不确定；一题问几件事就逐件答。
6. **资料答不上就看源码**（可选）：bot 用自己的模型一步步搜、读项目的源码，确认过的结论再补答。
7. **答不上就转交**：由 bot 私聊转给维护资料的人；在控制台「资料」页补的回答马上能被检索到。

向量和重排模型是本机的 llama.cpp `llama-server`（`--embedding`、`--reranking`），在控制台填地址；没有它们时退回关键词检索。

## 技术栈

| 部分 | 用的是 |
| --- | --- |
| 后端 | Python 3.12，asyncio，FastAPI + uvicorn，SQLite（aiosqlite，编号迁移），httpx，pydantic；uv 管依赖，ruff 检查，pytest 测试（全部离线：假 OneBot、假 Discord、假企业微信、假模型） |
| 控制台 | React + TypeScript + Vite + Tailwind + shadcn/ui，构建后由后端直接提供 |
| 桌面外壳 | Electron（Windows 托盘和启动器，可选） |
| QQ | OneBot 11（HTTP + 正向 WebSocket），断线自动重连并补收消息；一个号一个 NapCat Docker 容器，控制台能建容器、扫码、重启 |
| Discord | 自己写的 Gateway + REST 客户端（httpx、websockets），支持代理 |
| 企业微信 | 智能机器人长连接 |
| 模型接口 | OpenAI 兼容的 Chat Completions、OpenAI Responses、Anthropic Messages、ChatGPT 的 Codex 订阅；每个 bot 可设一个备用模型 |

## 部署

三种方式，各自的步骤见：

| 方式 | 适合 | 说明 |
| --- | --- | --- |
| Windows 直接安装 | 一台常开的 Windows 电脑 | [deploy/windows/README.md](deploy/windows/README.md) |
| Mac 直接安装 | Mac mini 这类常开的 Mac | [deploy/mac/README.md](deploy/mac/README.md) |
| Docker | Linux 服务器、NAS | [deploy/docker/README.md](deploy/docker/README.md) |

接 QQ 都要用 Docker 跑 NapCat 容器；只用 Discord 或企业微信时不需要。直接安装时的常用命令：

```bash
uv run nest start            # 在后台启动并打开控制台
uv run nest stop
uv run nest status
uv run nest set-password     # 设置从其他设备登录控制台的密码
uv run nest service install  # macOS：装成登录后自动启动的后台服务
```

- 控制台：`http://127.0.0.1:18800`；开启远程访问后，局域网和 Tailscale 的设备用密码登录。
- QQ 容器：在控制台 Bot 页「增加 bot」，nest 会自己建 NapCat 容器；想自己管理，照 `deploy/mac/docker-compose.example.yml` 写 compose。扫码在「系统」页点「扫码登录」。
- Discord 和企业微信：在控制台 Bot 页「增加 bot」选平台，填 Token 或 BotID 和 Secret。
- 设置和密钥在 `.env`（见 `.env.example`），数据库和日志在 `data/`，都不进 git。

## 开发

需要 [uv](https://docs.astral.sh/uv/) 和 Node.js 24。

```bash
uv sync                      # 安装后端依赖
uv run nest serve            # 前台运行后台服务
uv run pytest                # 测试（全部离线）
uv run ruff check . && uv run ruff format --check .
```

```bash
cd web
npm install
npm run dev                  # 控制台开发服务器，/api 代理到 127.0.0.1:18800
npm run build                # 构建到 web/dist，由后台直接提供
```

开发时先用 `uv run nest start --no-browser` 或 `uv run nest serve` 启动后台，再打开
`http://127.0.0.1:18800/login?token=<data/runtime.json 里的 token>` 登录一次，之后 `npm run dev` 的页面也能用同一个登录状态。

## 目录

```
src/nest/
  app.py              组装各组件
  cli.py              nest serve / start / stop / status / service …
  config.py           进程设置（NEST_* 环境变量或 .env）
  adapters/           平台适配器：onebot11（QQ）、discord、wecom（企业微信）
  core/               回复判断和提示词（brain、prompt）、截图读字、表情包、黑话
  knowledge/          项目资料：读资料、关键词 / 向量 / 重排检索、模型挑资料、看源码、转交没答上的问题
  llm/                模型接口
  watch/              监控（订阅、网页变化、价格）
  api/                控制台用的接口和访问控制
  storage/            SQLite 与编号迁移
tests/                全部离线；fakes/ 里是假的平台和模型
web/                  控制台（React + Vite + Tailwind + shadcn/ui）
desktop/              Windows 桌面外壳（Electron）
deploy/               各种部署方式：windows、mac（QQ 容器模板、每周重启脚本、launchd 配置）、docker
Dockerfile            Docker 镜像：后端、构建好的控制台和 docker 命令
```

## 许可证

[AGPL-3.0](LICENSE)。改了代码拿去对外提供服务，也要按同样的许可证公开改过的代码。
