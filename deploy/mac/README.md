# 在 Mac 上部署 nest

nest 原生运行（uv），每个 bot 的 QQ 跑在一个 NapCat 容器里（无界面的 Linux QQ，自带 OneBot 11）。下面以 Mac mini 为例，其他能跑 Docker 的机器也差不多。Mac 版 QQ 挡注入，所以 QQ 跑在 Docker 里的 Linux QQ 上。

> **风险**：NapCat 这类程序是用个人 QQ 号挂 bot，不是腾讯官方的机器人接口，违反 QQ 的用户协议。号可能被提示「风险设备」、限制功能甚至冻结。请用专门的小号，不要用主号。

## 一、准备机器

1. 系统设置：
   - 「能耗」里打开「断电后自动启动」和「防止自动进入睡眠」（或 `sudo pmset -a sleep 0 autorestart 1`）。
   - 「用户与群组」里打开自动登录：Docker 和 nest 服务都在登录后才启动。自动登录要求关闭 FileVault，这是一个取舍：机器被偷时磁盘没有加密。
2. 想在外面用手机打开控制台，装 Tailscale。
3. 装 Docker：OrbStack 或 Docker Desktop，设成登录时启动。OrbStack 建议把内存上限设成 4GB：`orbctl config set memory_mib 4096`。
4. 模型接口或 Discord 需要代理时，装一个本机代理，在控制台里填它的地址。
5. 装开发工具：`xcode-select --install`，再装 [uv](https://docs.astral.sh/uv/)。

## 二、QQ 容器

1. `cd deploy/mac && cp docker-compose.example.yml docker-compose.yml`，照里面的注释给每个 bot 号写一个服务：QQ 号、端口、文件夹，以及固定的 `hostname` 和 `mac_address`（QQ 把它们当设备身份，扫码以后不要再改）。`docker-compose.yml` 不进 git。
2. 同一个文件夹的 `.env` 里写一行 `NAPCAT_WEBUI_TOKEN=<一个长密码>`：NapCat 管理页的密码。
3. 每个号的 `config/onebot11.json` 里开一个 HTTP 服务（容器里的 3040）和一个 WebSocket 服务（3041），`host` 填 `0.0.0.0`，令牌和 nest `.env` 里的 `NEST_QQ_<QQ号>_HTTP_TOKEN`、`NEST_QQ_<QQ号>_WS_TOKEN` 一样。
4. `docker compose up -d`。
5. 扫码：nest 跑起来以后，在控制台「系统」页那个号的容器点「扫码登录」，二维码直接显示在控制台里。还没有 nest 时，用 SSH 转发管理页端口，打开 `http://127.0.0.1:<管理页端口>/webui`。二维码约 2 分钟换一张。
   - **重启后自动登录**：每个服务设了 `ACCOUNT`，NapCat 启动时先用保存的登录快速登录。
   - **登录失效时不一直刷二维码**：连着两次在等扫码、又没人在控制台打开二维码的容器，nest 会停下它（`src/nest/qr_guard.py`）。在手机上解除限制后，再到「系统」页点「扫码登录」。
   - **掉线提醒**：「系统」页「掉线提醒」填一个 QQ 号，某个号断开超过设定的分钟数时，还在线的 bot 会私聊它。
   - **回退密码（可选）**：「系统」页每个容器有「回退密码」，nest 只存密码的 MD5。保存的登录失效时 NapCat 先用密码登录。MD5 也能用来登录这个号，所以 `.env` 要当密码一样保管。
6. 也可以不写 compose：在控制台 Bot 页点「增加 bot」，nest 自己建容器 `napcat-<QQ号>`，主机名和网卡地址由 QQ 号算出来、每次都一样。
7. 每周自动重启一次 QQ 容器（QQ 跑久了内存会慢慢涨）：把 `com.nest.qq-restart.plist` 里的路径改成你的，再 `cp com.nest.qq-restart.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nest.qq-restart.plist`。它每周一 5:00 逐个重启在运行的容器。

## 三、nest

1. 在仓库根目录 `uv sync --frozen`，再 `cd web && npm ci && npm run build` 构建控制台。
2. 设置登录密码：`uv run nest set-password`。
3. 开启远程访问：`.env` 里加一行 `NEST_REMOTE=true`（或在「系统」页「远程访问」里打开）。只有本机、局域网和 Tailscale 的地址能连，外网的访问一律拒绝；不要在路由器上给 18800 做端口转发，也不要在前面加反向代理。
4. 装成后台服务：`uv run nest service install`，登录后自动启动、崩溃自动重启。
5. 控制台里：「模型」页加模型，「Bot 页」加 bot 并选人设和模型，「QQ 连接」填每个号的 `http://127.0.0.1:<HTTP 端口>` 和 `ws://127.0.0.1:<WebSocket 端口>`，先「测试连接」再保存。「系统」页「位置和网络」运行方式选「Docker 容器」，「QQ 容器」填容器名，用逗号隔开。
6. 用 Codex 订阅模型时，在这台机器上单独 `codex login`，不要从别的机器复制登录信息。

## 四、项目资料的模型服务（可选）

项目资料的向量和重排模型用 llama.cpp 的 `llama-server`：一个进程开 `--embedding` 跑向量模型，另一个开 `--reranking` 跑重排模型，都只监听 127.0.0.1。在 `.env` 里填它们的地址（见 `src/nest/config.py` 里的 `NEST_KNOWLEDGE_*`）。没有它们时，项目问题只用关键词检索。
