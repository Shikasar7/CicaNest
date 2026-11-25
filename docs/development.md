# 开发

代码和命令里叫 nest：一个后台进程管多个 bot，每个 bot 有自己的账号、名字、头像、人设和模型。

## 技术栈

| 部分 | 用的是 |
| --- | --- |
| 后端 | Python 3.12，asyncio，FastAPI + uvicorn，SQLite（aiosqlite，编号迁移），httpx，pydantic；uv 管依赖，ruff 检查，pytest 测试（全部离线：假 OneBot、假 Discord、假 Telegram、假 Slack、假企业微信、假模型） |
| 控制台 | React + TypeScript + Vite + Tailwind + shadcn/ui，构建后由后端直接提供 |
| 桌面外壳 | Electron（Windows 托盘和启动器，可选） |
| QQ | OneBot 11（HTTP + 正向 WebSocket），断线自动重连并补收消息；一个号一个 NapCat Docker 容器，控制台能建容器、扫码、重启 |
| Discord | 自己写的 Gateway + REST 客户端（httpx、websockets），支持代理 |
| Telegram | 自己写的 Bot API 客户端（httpx，长轮询），支持代理 |
| Slack | 自己写的 Socket Mode + Web API 客户端（websockets、httpx），支持代理 |
| 企业微信 | 智能机器人长连接 |
| 模型接口 | OpenAI 兼容的 Chat Completions、OpenAI Responses、Anthropic Messages、ChatGPT 的 Codex 订阅；每个 bot 可设一个备用模型 |

## 本地运行

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

## 常用命令

```bash
uv run nest start            # 在后台启动并打开控制台
uv run nest stop
uv run nest status
uv run nest set-password     # 设置从其他设备登录控制台的密码
uv run nest service install  # macOS：装成登录后自动启动的后台服务
```

设置和密钥在 `.env`（见 `.env.example`），数据库和日志在 `data/`，都不进 git。

## 目录

```
src/nest/
  app.py              组装各组件
  cli.py              nest serve / start / stop / status / service …
  config.py           进程设置（NEST_* 环境变量或 .env）
  adapters/           平台适配器：onebot11（QQ）、discord、telegram、slack、wecom（企业微信）
  core/               回复判断和提示词（brain、prompt）、截图读字、表情包、黑话
  knowledge/          项目资料：读资料、关键词 / 向量 / 重排检索、模型挑资料、看源码、转交没答上的问题
  llm/                模型接口
  watch/              监控（订阅、网页变化、价格、Discord 开放）
  api/                控制台用的接口和访问控制
  storage/            SQLite 与编号迁移
tests/                全部离线；fakes/ 里是假的平台和模型
web/                  控制台（React + Vite + Tailwind + shadcn/ui）
desktop/              Windows 桌面外壳（Electron）；`npm run dist` 打成自带后台的安装包（scripts/build-backend.js 把
                      Python、依赖和 web/dist 放进去，scripts/installer.nsh 管装在哪、升级前停后台），也能只连远程的 nest；
                      src/update*.js 是应用内更新，更新源是 package.json 的 updates
deploy/               各种部署方式：windows、mac（QQ 容器模板、每周重启脚本、launchd 配置）、docker
Dockerfile            Docker 镜像：后端、构建好的控制台和 docker 命令
```
