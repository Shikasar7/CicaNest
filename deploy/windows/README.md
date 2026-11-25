# 在 Windows 上部署 nest

两种装法：用安装包（推荐，什么都不用另装），或者从源码跑（要改代码，或者想用命令行时）。只用 Discord、Telegram、Slack 或企业微信时，装好就能用；接 QQ 还要装 Docker Desktop，每个 QQ 号一个 NapCat 容器，由控制台创建和管理。

> **风险**：NapCat 这类程序是用个人 QQ 号挂 bot，不是腾讯官方的机器人接口，违反 QQ 的用户协议。号可能被提示「风险设备」、限制功能甚至冻结。请用专门的小号，不要用主号。

## 一、用安装包

到 [Releases](https://github.com/Shikasar7/CicaNest/releases/latest) 下载 `nest-setup-版本.exe`。里面有 nest 的后台、控制台和桌面应用，自带 Python。

- **装在哪**：安装时随便选。选空文件夹或新文件夹就正好装在那里；文件夹里已经有别的东西，会在里面另建一个 `nest` 文件夹（装之前会告诉你），卸载时不会碰你自己的文件。装到 `Program Files` 这类要管理员权限的地方，在前一页选为所有用户安装。路径不超过 120 个字。
- **第一次打开**：选「在这台电脑上」就在本机跑；选「在另一台电脑上」只连过去，不在本机启动后台。以后在托盘菜单「连接设置…」里换，换的时候会问要不要停掉本机的 nest。
- **数据**：`%APPDATA%\nest\home`，里面是 `.env`（密钥、端口）和 `data\`（数据库、日志、伙伴的文件）。和装在哪无关，升级、卸载都不删；备份就备份这个文件夹。QQ 容器的数据在旁边的 `%APPDATA%\nest\qq`。
- **端口**：默认 18800。第一次选「在这台电脑上」时 18800 已被占用的话，会换下一个空的，写进 `.env`。
- **升级**：0.8.0 起在应用里更新。打开后一分钟和之后每 12 小时问一次 GitHub 的 Releases（走系统代理；可以在控制台「系统」页的「桌面应用」里关掉，也可以在托盘菜单里手动检查）；点「更新」才下载，文件和发布的 SHA-256 对不上就删掉；下载好再问一次才装，先正常停掉 nest，装到原来的位置后自己打开。装给所有用户的会打开安装界面，要管理员同意。没装上的话，下次打开会告诉你。连着另一台 nest 时，更新的只是这台的桌面端，那台要自己更新。
- **自己下载来升级**：直接装新版本就行，同样会先停好正在跑的 nest；从只有客户端的旧版本（0.7.0 和更早）升上来，原来连的地址照旧。换回旧版本前先备份数据：新版本升级过的数据库，旧版本打不开（会提示，不会写坏）。
- **退出**：托盘菜单的「退出」只关桌面应用，伙伴继续在线；「退出并停止 nest」连后台一起停。想随 Windows 启动，勾上「开机时自动启动」。

安装包没有代码签名：Windows 第一次会提示「已保护你的电脑」，点「更多信息」→「仍要运行」；打开了「智能应用控制」的电脑可能不让运行。

## 二、从源码跑

需要 [Git](https://git-scm.com/)、[uv](https://docs.astral.sh/uv/) 和 [Node.js](https://nodejs.org/) 24。

```powershell
git clone https://github.com/Shikasar7/CicaNest.git
cd CicaNest
uv sync --frozen
cd web; npm ci; npm run build; cd ..
```

双击 `启动.bat`：nest 在后台启动并打开控制台（`http://127.0.0.1:18800`）。`停止.bat` 停止它。本机打开控制台不用密码。只装了 uv、没有 Node.js 时，用 Releases 里的 `CicaNest-版本-server.zip`，控制台已经构建好，解压后直接双击 `启动.bat`。

桌面应用（托盘、聊天窗口、桌宠）也能从源码跑：

```powershell
cd desktop
npm ci
npm run shortcuts   # 在桌面和开始菜单放「nest」快捷方式
```

以后从快捷方式打开即可。自己打安装包：先构建好控制台（上面的 `npm run build`），再在 `desktop` 里运行 `npm run dist`，安装包在 `desktop\dist`。

## 三、接 QQ（Docker Desktop + NapCat）

1. 装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（用 WSL 2 后端），在它的设置里打开「登录 Windows 时启动」。
2. 在控制台「模型」页加一个模型，再到「Bot」页点「增加 bot」，选 QQ，填名字和 QQ 号。nest 会自己建容器 `napcat-<QQ号>`，QQ 的登录和缓存放在 nest 文件夹旁边的 `qq` 文件夹里（安装包是 `%APPDATA%\nest\qq`）。
3. 在「系统」页那个容器点「扫码登录」，用这个号的手机 QQ 扫码。之后容器或电脑重启，QQ 会自己登录。
4. 「系统」页「位置和网络」的运行方式是「Docker 容器」（默认）。

想自己写 compose 管容器，参照 [deploy/mac/docker-compose.example.yml](../mac/docker-compose.example.yml)，写法在 Windows 上也一样。

## 四、从其他设备打开控制台（可选）

1. 在控制台「系统」页的「远程访问」里设置登录密码（从源码跑的也可以用 `uv run nest set-password`）。
2. 在同一处打开「远程访问」（或在 `.env` 里加一行 `NEST_REMOTE=true`），重启 nest。Windows 防火墙第一次会问是否允许 Python 访问网络，只勾「专用网络」。
3. 局域网里的设备打开 `http://<这台电脑的局域网 IP>:18800`，用密码登录。只有本机、局域网和 Tailscale 的地址能连，外网一律拒绝；不要在路由器上给 18800 做端口转发。

## 常见问题

- **时区**：nest 用 Windows 的当前时区；QQ 容器的日志时间按同样的时差显示。
- **端口被占用**：在 `.env` 里写 `NEST_PORT=其他端口`（安装包的在 `%APPDATA%\nest\home\.env`，没有就新建一个）。
- **日志**：`data\logs\nest.log`，或者托盘菜单的「打开日志文件夹」。
