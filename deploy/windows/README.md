# 在 Windows 上部署 nest

nest 直接跑在 Windows 上（uv 管 Python）。只用 Discord 或企业微信时，装好就能用；接 QQ 还要装 Docker Desktop，每个 QQ 号一个 NapCat 容器，由控制台创建和管理。

> **风险**：NapCat 这类程序是用个人 QQ 号挂 bot，不是腾讯官方的机器人接口，违反 QQ 的用户协议。号可能被提示「风险设备」、限制功能甚至冻结。请用专门的小号，不要用主号。

## 一、安装

需要 [Git](https://git-scm.com/)、[uv](https://docs.astral.sh/uv/) 和 [Node.js](https://nodejs.org/) 24。

```powershell
git clone https://github.com/Shikasar7/CicaNest.git
cd CicaNest
uv sync --frozen
cd web; npm ci; npm run build; cd ..
```

双击 `启动.bat`：nest 在后台启动并打开控制台（`http://127.0.0.1:18800`）。`停止.bat` 停止它。本机打开控制台不用密码。

## 二、桌面应用（可选）

`desktop` 是一个托盘小程序：显示 nest 和各个 bot 的状态，掉线时提醒，右键菜单里能打开控制台、重启 nest，以及开关「开机时自动启动」。

```powershell
cd desktop
npm ci
npm run shortcuts   # 在桌面和开始菜单放「nest」快捷方式
```

以后从快捷方式打开即可。想让 nest 随 Windows 启动，在托盘菜单里勾上「开机时自动启动」。

## 三、接 QQ（Docker Desktop + NapCat）

1. 装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（用 WSL 2 后端），在它的设置里打开「登录 Windows 时启动」。
2. 在控制台「模型」页加一个模型，再到「Bot」页点「增加 bot」，选 QQ，填名字和 QQ 号。nest 会自己建容器 `napcat-<QQ号>`，QQ 的登录和缓存放在项目文件夹旁边的 `qq` 文件夹里。
3. 在「系统」页那个容器点「扫码登录」，用这个号的手机 QQ 扫码。之后容器或电脑重启，QQ 会自己登录。
4. 「系统」页「位置和网络」的运行方式是「Docker 容器」（默认）。

想自己写 compose 管容器，参照 [deploy/mac/docker-compose.example.yml](../mac/docker-compose.example.yml)，写法在 Windows 上也一样。

## 四、从其他设备打开控制台（可选）

1. 设置登录密码：`uv run nest set-password`。
2. 在项目文件夹的 `.env` 里加一行 `NEST_REMOTE=true`（或在「系统」页「远程访问」里打开），重启 nest。Windows 防火墙第一次会问是否允许 Python 访问网络，只勾「专用网络」。
3. 局域网里的设备打开 `http://<这台电脑的局域网 IP>:18800`，用密码登录。只有本机、局域网和 Tailscale 的地址能连，外网一律拒绝；不要在路由器上给 18800 做端口转发。

## 常见问题

- **时区**：nest 用 Windows 的当前时区；QQ 容器的日志时间按同样的时差显示。
- **端口被占用**：在 `.env` 里写 `NEST_PORT=其他端口`。
- **日志**：`data\logs\nest.log`。
