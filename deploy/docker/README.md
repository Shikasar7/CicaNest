# 用 Docker 部署 nest（Linux 服务器）

nest 和它的控制台在一个容器里；每个 QQ 号再各用一个 NapCat 容器，由控制台创建和管理。适合一台常开的 Linux 服务器或 NAS。Windows 和 Mac 上更推荐直接安装（[Windows](../windows/README.md)、[Mac](../mac/README.md)）：Docker Desktop 不支持下面用到的主机网络。

> **风险**：NapCat 这类程序是用个人 QQ 号挂 bot，不是腾讯官方的机器人接口，违反 QQ 的用户协议。号可能被提示「风险设备」、限制功能甚至冻结。请用专门的小号，不要用主号。

## 一、启动

需要 Docker Engine 和 Docker Compose。

```bash
git clone https://github.com/Shikasar7/CicaNest.git
cd CicaNest/deploy/docker
docker compose up -d --build
docker compose exec -it cicanest nest set-password
```

然后在局域网里的设备上打开 `http://<服务器的局域网 IP>:18800`，用刚设的密码登录。

数据都在服务器的 `/opt/cicanest` 下：`nest/` 是 nest 的数据库、日志和 `.env`（模型密钥、令牌），`qq/` 是各个 QQ 号的登录和缓存。备份就备份这个文件夹。要换位置，在启动前设置 `CICANEST_DIR`（比如写进本文件夹的 `.env`：`CICANEST_DIR=/srv/cicanest`）。

## 二、添加 bot

1. 「模型」页加一个模型。
2. 「Bot」页点「增加 bot」，选平台：
   - **QQ**：填名字和 QQ 号。nest 会建容器 `napcat-<QQ号>`，再到「系统」页那个容器点「扫码登录」，用这个号的手机 QQ 扫码。
   - **Discord**、**企业微信**：填 Token 或 BotID 和 Secret，不需要别的容器。
3. 连上以后，在 bot 的设置里打开它要说话的群。

## 三、为什么这样配置

- **主机网络**（`network_mode: host`）：nest 按连接方的真实地址决定放不放行（只认本机、局域网和 Tailscale），也要在 127.0.0.1 上连到它建的 QQ 容器。
- **挂上 Docker**（`/var/run/docker.sock`）：控制台要能启动、停止 QQ 容器，读扫码用的二维码，增加 bot 时新建容器。这等于把服务器的 Docker 交给 nest，所以控制台的密码要设得长一点。
- **同路径挂载 `CICANEST_DIR`**：nest 建的 QQ 容器从 `CICANEST_DIR/qq` 挂载数据，这个路径由服务器上的 Docker 解析，所以容器里外要一样。
- 容器里以 root 运行，建出来的文件属于 root。

## 常见问题

- **更新**：`git pull`，再 `docker compose up -d --build`。
- **时区**：默认 `Asia/Shanghai`，在本文件夹的 `.env` 里写 `TZ=...` 改。
- **日志**：`docker compose logs -f cicanest`，或 `/opt/cicanest/nest/data/logs/nest.log`。
- **换端口**：在 `/opt/cicanest/nest/.env` 里写 `NEST_PORT=其他端口`，再 `docker compose restart`。
