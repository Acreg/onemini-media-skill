# Onemini 多媒体创作 Skill

把“帮我画图 / 做视频 / 创作音乐”变成一次完整的 API 工作流：**选型号 → 准备参数 → 提交 → 查询 → 下载作品**。

这是面向智能体的可安装技能，不是网页应用，也不是模型本身。内置参数说明可离线读取；实际生成需要联网、Python 3.10+ 和用户自己的 **Onemini 创作密钥**。仅使用 Python 标准库，无需安装第三方运行依赖。

## 能做什么

- 图片生成与文档支持的参考图编辑、透明背景、GIF、SVG 等能力。
- 视频生成与文档支持的首尾帧、多模态、编辑、续写、草稿转正式等模式。
- Suno、Flowmusic 音乐生成，按各型号实际能力使用。
- 本地参考图片/视频上传，真实任务查询、恢复和多产物下载。
- 真实模型目录、可选估价、按接口能力请求外发前停止。
- 到期自动检查正式说明；发现实际变化后询问是否更新本地参数资料。

2026-11-15 文档快照收录 **59 个图片、61 个视频、2 个音乐精确型号**，共31篇正式多媒体说明。文档覆盖不代表每把 Key 都有全部型号的权限，也不代表全部型号都逐个做过真实生成测试。

## 让智能体安装

可以直接告诉你的智能体：

> 请从 https://github.com/Acreg/onemini-media-skill 安装 onemini-media 技能到你的技能目录，阅读 SKILL.md 并初始化配置。不要读取、打印或索要我的真实密钥，我会在本地填写。

支持文件式 `SKILL.md` 的智能体可以使用；不同产品的自动发现目录不同，以该产品说明为准。**仓库根目录本身就是技能根目录**，安装后的文件夹建议命名为 `onemini-media`，不要只复制 SKILL.md。

### 推荐：Python 两步安装

只需 **Python 3.10+**，无需 Git、Node.js 或 pip。在准备安装时使用的**任意空目录**打开终端，不必进入仓库或项目根目录。

**第一步：下载安装器。**

```sh
python -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/Acreg/onemini-media-skill/main/install.py', 'install-onemini.py')"
```

**第二步：指定自己的智能体 skills 父目录。** Windows 示例（请替换成你的实际目录）：

```sh
python install-onemini.py --目录 "D:/智能体工具/我的 Skills"
```

`--目录` 填的是 **skills 父目录，不是 `onemini-media` 自身**；上例会安装到 `D:/智能体工具/我的 Skills/onemini-media`。

macOS / Linux 可将第二步换成以下示例；`$HOME/.agents/skills` **不是所有智能体的默认目录**，以你自己的智能体技能目录为准：

```sh
python install-onemini.py --目录 "$HOME/.agents/skills"
```

若系统使用 `python3` 命令，请将两步中的 `python` 都换成 `python3`。

安装器固定下载公开 Release 的 **v0.1.0 ZIP** 并校验 **SHA-256**，不是自动安装最新版。安装无需 Key，不调用收费接口，也没有解锁步骤。首次安装会创建 `【务必安全使用不要打印出来】.env`，**不需再手动初始化**；重新安装会保留已有 `.env` 和用户其他文件。按下一节在本地填好配置后，**重载智能体**以发现技能。

### 手动选项：Git 或 ZIP

手动方式：进入你的智能体技能目录后执行：

```sh
git clone https://github.com/Acreg/onemini-media-skill.git onemini-media
cd onemini-media
python scripts/onemini.py 初始化
```

也可以从仓库下载 ZIP，把包含 SKILL.md 的完整目录放入技能目录。安装新版本时保留自己的 .env，不要用空配置覆盖它。

## 在本地填写密钥

初始化会创建技能根目录的：

```text
【务必安全使用不要打印出来】.env
```

用本地文本编辑器填写，**不要把文件内容发到聊天、截图或提交到仓库**：

```dotenv
ONEMINI_API_KEY=在本地填写你自己的创作密钥
ONEMINI_BASE_URL=https://api.onemini.top
```

- 初始化不会覆盖已经存在的配置。
- 脚本按自身位置定位技能根目录，从其他工作目录运行也不会读错 .env。
- 可用同名进程环境变量显式配置；不要把真实 Key 写在命令行参数里。
- 公开仓库只有 `.env.example` 占位示例；真实 .env、运行缓存和作品被 Git 忽略。
- 警告文件名只是提醒，保护来自不回显、不提交和下载去认证。

正常生成会使用相应模型额度或余额，费用以当前 Key 的报价与最终账单为准。用户要求生成后会直接真实调用，不需要再开启“允许真实请求”或开发解锁。

## 最简单的使用方式

填写 Key 后，直接向智能体描述需求，例如：

> 用 Onemini 的 gpt-image-2.5-flare 生成一张 1:1、1k 的橘猫图片，把作品保存到当前项目。

> 用 seedance-2.0 做一段 5 秒、16:9 的海边灯塔视频，带声音。生成好后下载给我。

> 用 Suno v6 做一段适合旅行视频的纯音乐，拿到全部音轨并保存。

智能体应先找 [精确型号索引](references/models.md)，只读目标型号文档，不把别的型号参数照搬过来。

## 手动调用示例

以下内容是**参数文件的内容**，不包含 Key。将对应 JSON 保存到你的任务工作区；示例型号是否可用由当前 Key 的目录决定。输出目录建议放任务工作区，不放技能源码目录。

### 图片

保存为 `image.json`：

```json
{
  "model": "gpt-image-2.5-flare",
  "prompt": "一只橘猫坐在窗台，午后自然光，摄影质感",
  "size": "1:1",
  "resolution": "1k",
  "n": 1
}
```

```sh
python scripts/onemini.py 生成 --类型 图片 --参数文件 image.json --输出目录 ./作品
```

### 视频

保存为 `video.json`：

```json
{
  "model": "seedance-2.0",
  "prompt": "镜头缓慢推进，海边灯塔在日落中亮起，海浪轻拍礁石",
  "size": "16:9",
  "duration": 5,
  "generate_audio": true
}
```

```sh
python scripts/onemini.py 生成 --类型 视频 --参数文件 video.json --输出目录 ./作品 --不等待
```

“不等待”只结束本地等待，不取消服务端任务。保存返回的任务记录路径，稍后执行 `继续`。

### Suno 音乐

保存为 `music.json`：

```json
{
  "model": "suno",
  "version": "v6",
  "custom": false,
  "prompt": "适合旅行短片的轻快原声吉他配乐",
  "instrumental": true
}
```

```sh
python scripts/onemini.py 生成 --类型 音乐 --参数文件 music.json --输出目录 ./作品
```

### Flowmusic 音乐

Flowmusic 使用不同字段；`bpm`、`seed` 是字符串：

```json
{
  "model": "flowmusic",
  "sound_prompt": "轻柔钢琴与弦乐，适合作为阅读背景音乐",
  "bpm": "90",
  "length": 30,
  "seed": "42",
  "version": "lyria-3.5"
}
```

保存为 `flowmusic.json` 后，同样用 `生成 --类型 音乐 --参数文件 flowmusic.json`。

## 查询、恢复、上传与下载

```text
python scripts/onemini.py 目录
python scripts/onemini.py 目录 --离线
python scripts/onemini.py 查询 <本站任务ID>
python scripts/onemini.py 继续 <保存的任务记录路径>
python scripts/onemini.py 任务 --业务标记 <client_ref>
python scripts/onemini.py 下载 <本站任务ID> --输出目录 <作品目录>
python scripts/onemini.py 上传 <本地图片路径> --类型 图片 --模型 seedance-2.0
python scripts/onemini.py 上传 <本地视频路径> --类型 视频 --模型 <支持视频参考的型号>
python scripts/onemini.py 估价 --类型 视频 --参数文件 <参数文件路径>
```

`估价` 是可选的，不是生成前置条件。已有可用 HTTPS 素材直链时无需先上传。统一素材上传只支持图片和视频，不虚构音频上传入口。

完整说明看 [任务指南](references/tasks.md) 和每个子命令的 `--help`。

## 线路选择

| 线路 | 基础地址 |
|---|---|
| 默认主站 | `https://api.onemini.top` |
| 香港 | `https://api-hk.onemini.top` |
| 亚太 | `https://api-ap.onemini.top` |

可以编辑配置的基础地址，或在子命令前指定：

```text
python scripts/onemini.py --线路 香港 目录
python scripts/onemini.py --线路 亚太 生成 --类型 图片 --参数文件 <参数文件路径>
```

基础地址不带 `/v1`。如果已经发送生成请求但超时，先用原记录核对任务，不直接换线路再生成；不同线路的任务/幂等是否共享必须以实际服务契约为准。

## 文档更新：先检查，发现变化后询问

正式入口：`https://docs-api.onemini.top/llms.txt`。它是索引，完整参数在其链接的正文中。

- 使用技能时按时间检查，默认7天一次；不是安装常驻服务。
- 检查同时比较参数正文，索引不变也能发现正文变化。
- 有变化时告知内置快照日期、检查日期和变化内容，询问是否更新。
- 用户暂不更新或文档站连接失败时仍可继续使用，不设置生成门禁。
- 用户同意后只更新参数文档和清单，不改密钥、程序、主 SKILL.md 或作品。

```text
python scripts/sync_docs.py 检查
python scripts/sync_docs.py 检查 --强制
python scripts/sync_docs.py 更新 --确认
```

`更新 --确认` 仅在用户已经同意更新参数说明后执行，不需要为正常生成反复确认。不要用自动 git pull 替代文档同步；代码升级应明确安装新版本并保留本地配置。

## 常见错误

见 [中文排障指南](references/errors.md)。输出会区分 HTTP 失败、异步任务失败、提交状态未知与下载失败。**返回任务号不等于作品已生成，本地停止等待也不等于取消服务端任务。**

需要外发前停止能力时，按目录能力使用 `准备`、生成的 `--请求标识`、`停止` 命令，协议见 [任务指南](references/tasks.md)。已经外发的模型请求不能用本地停止轮询撤销。

## 仓库结构

```text
SKILL.md                  给智能体的短入口
README.md                 安装和使用说明
install.py                固定 v0.1.0 的 Python 独立安装器
docs-manifest.json        来源、快照时间、正文摘要、型号映射
references/models.md      完整精确型号索引
references/images/        图片参数原文
references/videos/        视频参数原文
references/music/         音乐参数原文
references/tasks.md       任务、上传、恢复和停止
references/errors.md      中文排障
scripts/onemini.py         调用工具
scripts/sync_docs.py       文档检查和同步
tests/                    隔离验证，不是正式业务路径
```

参数原文保留来源字节，部分正文跨页链接指向正式站；离线阅读可通过本地型号索引和公共任务指南定位对应文档。文档中的示例占位素材不能直接用于真实生成。

## 验证与维护

```sh
python -m unittest discover -s tests -v
```

v0.1.0 已通过60项隔离测试；另外匿名检查了三条API线路的鉴权入口和正式文档正文。尚未使用本任务授权的Key进行收费生成，真实图片/视频/音乐端到端仍未验收。隔离测试不能替代真实生成，也不代表全部122型号已实测。安装、初始化和文档检查不触发收费生成；收费操作只由用户发起正常业务请求时执行。

发布代码采用 MIT 许可。正式参数来源保留在 `docs-manifest.json`，线上变化可通过文档检查工具获取。
