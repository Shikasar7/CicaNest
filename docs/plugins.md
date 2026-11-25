# 写一个 nest 插件

插件就是一个文件夹（或者它的 zip），最上层有一个 `plugin.json`。在控制台「插件」页可以装 zip；也可以放进插件市场的清单里，让别人从「插件市场」装。

装之前，nest 会把插件要的权限和技能全文给用户看，用户点了「安装」才会装。装好以后，插件默认对谁都不开，要用户在「插件」页里勾给哪个伙伴。

## plugin.json

```json
{
  "id": "yourname.weather",
  "version": "1.0.0",
  "name": "天气",
  "author": "yourname",
  "description": "一句话说清它是干什么的",
  "provides": {
    "skills": ["skills/weather.md"]
  },
  "permissions": {
    "network": ["api.weather.example"],
    "secrets": [{ "name": "WEATHER_KEY", "label": "天气服务的 key" }],
    "confirm": ["send_alert"]
  }
}
```

| 字段 | 要求 |
|---|---|
| `id` | `作者.名字`，小写字母、数字、点和 `-`；`nest.` 开头的留给 nest 自己 |
| `version` | `1.0.0` 这样，改了插件就往上加，用户那边会显示「能升级」 |
| `name` | 显示的名字 |
| `author`、`description` | 可不填，建议写 |
| `nest` | 可不填。要 nest 几以上才能用，写最低版本号，比如 `0.6.0` |
| `provides` | 它提供什么，下面一节 |
| `permissions` | 它要什么，再下面一节 |

## provides：插件能提供的东西

| 写法 | 是什么 | 信任等级 |
|---|---|---|
| `"skills": ["skills/x.md", …]` | 技能：一篇 Markdown，教伙伴怎么做一件事。开头可以写 front matter 的 `name:` 和 `description:`，不写就用第一个标题和第一行。伙伴需要时用 `read_skill` 读出全文照着做 | guides：会指挥伙伴做事，装之前用户要看全文 |
| `"themes": ["themes/sakura.json", …]` | 主题：只有颜色。文件名是小写字母、数字和 `-`。格式可以在控制台「系统 → 外观」导出一份来看 | content |
| `"pets": ["pets/xx", …]` | 桌宠：文件夹里有 `pet.json`（一只桌宠或一套皮肤）或 `acc.json`（一个配饰） | content |
| `"stickers": ["stickers/xx", …]` | 一套表情包：文件夹里有 `pack.json`（`{"name": "…", "items": [{"mood": "开心", "file": "01.png"}]}`）和图（png、gif、webp、jpg，每张 8 MB 以内，最多 60 张）。装好后在聊天页「表情包」里拿给哪个伙伴用。在那里也能把画好的一套直接存成插件包 | content |
| `"companion": "companion"` | 一整个伙伴（人设、说话示例），格式和控制台「导出伙伴」得到的一样 | guides |
| `"mcp": {"command": [...], "install": [...]}` | MCP 工具服务器。`install` 是装的时候在伙伴容器里跑的命令，`command` 是启动服务器的命令和参数。它在每个伙伴自己的容器里跑（Linux，有 Python 3.12 和 Node 22），只看得到自己的插件文件夹 | code |
| `"python": {"module": "模块名"}` | 在 nest 进程里跑的 Python 模块，用来加模型种类、聊天平台 | trusted：只有用户勾了「我信任它」才装，市场里标红 |

## permissions：插件要的东西

- `network`：它的容器要访问的网站，写域名，比如 `api.example.com`，或者 `*.example.com`（这个域名和它下面的所有子域名），`*` 表示任意公网网站。本机和局域网无论怎么写都连不上。
  - 控制台插件卡片上的「它实际连了哪些网站」，会把它的容器实际访问过的网站和这里的声明对比，没声明的标红。
- `secrets`：要用户填的密钥。`name` 是大写字母、数字和下划线；系统和常见程序自己用的变量名不能用。用户在「插件」页里填，存在 nest 的 `.env` 里，只在调用时传给它的容器。
- `confirm`：每次调用都要用户点头的工具（比如会发消息、会花钱的）。用户点头之前，工具不会跑。

## 打包

把插件文件夹打成 zip：`plugin.json` 在 zip 的最上层，或者在 zip 里唯一的那个文件夹里。限制：

- zip 不超过 50 MB，解开后不超过 200 MB；
- 最多 5000 个文件；
- 不能有链接，也不能有 `..` 这种跑出文件夹的路径。

插件市场用的清单格式见下面「市场清单」。

## 签名

放进市场清单的插件可以带作者签名。签过名的插件，别人装了以后，升级时 nest 会核对是不是同一把钥匙签的，不是就不让装。

```powershell
nest plugin-key my-plugin.key
```

```powershell
nest plugin-sign yourname.weather-1.0.0.zip --key my-plugin.key
```

第一条做一把钥匙，只做一次，文件自己收好，别放进仓库；丢了就没法再用这个作者身份升级插件。第二条打印 `author_key` 和 `signature` 两行，写进市场清单里那个插件的条目。zip 改了就要重新签。

## 市场清单

清单是一个 https 地址上的 JSON：

```json
{
  "format": 1,
  "report": "https://github.com/you/your-plugins/issues/new?title=举报%20{id}%20{version}",
  "plugins": [
    {
      "id": "yourname.weather", "name": "天气", "version": "1.0.0", "author": "yourname",
      "description": "…", "kinds": ["skills"], "trust": "guides", "nest": "0.6.0",
      "url": "https://…/yourname.weather-1.0.0.zip", "sha256": "…", "size": 12345,
      "permissions": { "network": [], "secrets": [], "confirm": [] },
      "author_key": "…", "signature": "…"
    }
  ]
}
```

`report` 可不填，填了每个插件旁边有「举报」链接（`{id}`、`{version}` 会换成插件的）。装着的插件在清单里有了新版本，「插件」旁边会出数字提醒（每天看一次，不会自动装）。

## 例子

[examples/plugins/hello](../examples/plugins/hello) 是一个最小的插件：一篇技能。打包：

```powershell
Compress-Archive examples/plugins/hello/* hello.zip
```
