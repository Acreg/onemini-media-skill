# 多媒体 API 快速开始与模型总览

# 多媒体 API 快速开始与模型总览

本文介绍图片、视频和音乐模型的 API 接入。模型专属参数见本目录下的「图片模型」「视频模型」「音乐模型」；先按本页完成鉴权和模型发现，再阅读要用的具体型号。

## 1. 正式 API 地址与鉴权

图片、视频和音乐请求统一使用 Onemini 正式 API 地址：

```text
https://api.onemini.top
```

文档地址是 `https://docs-api.onemini.top`，它用于阅读说明，不是发送模型请求的 API 入口。

示例使用 Bash / zsh 语法，Windows 可在 WSL 或 Git Bash 中执行；HTTP 请求本身与操作系统无关。

```bash
export BASE_URL="https://api.onemini.top"
export API_KEY="替换成该站点签发的 API Key"
```

通用请求头：

```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

`BASE_URL` 不带末尾 `/v1`，下文路径已包含 `/v1`。若 SDK 把版本前缀作为 Base URL，则设置为 `https://api.onemini.top/v1`，避免拼出 `/v1/v1`。

## 2. 先确认自己的 Key 可以使用哪些模型

```bash
curl --fail-with-body "$BASE_URL/v1/media/models" \
  -H "Authorization: Bearer $API_KEY"
```

此接口返回当前 Key 可访问的媒体目录。主要读取 `data` 模型列表、`catalog_revision`、`policy_revision` 和 `warnings`；具体型号的参数契约见其参数元数据中的 `input_schema`。同一响应还提供 `models` 兼容字段，不要把两份列表重复计数。

- **匿名模型广场是资料展示**；能看到模型不代表该 Key 已有可用线路或报价。
- `model` 必须逐字使用目录中的完整 ID；`-official`、`-ext`、`-flare`、`-sunburst`、`-face` 等后缀不能省略或互换。
- 各型号的 `size`、`resolution`、`duration`、`seed`、参考素材等字段可能有不同类型和枚举，不能只改 `model` 就照搬另一个型号的全部参数。
- 参数表标记「未声明默认值」表示不要自行补一个默认值；省略后的行为由实际模型决定。
- 本组目录核对日期为 **2026-11-15（北京时间）**，覆盖 59 个图片型号、61 个视频型号、2 个音乐型号。语音合成、转写与音频对话不属于本组音乐文档范围。

## 3. 三类请求的最小示例

### 3.1 图片

```bash
curl --fail-with-body --max-time 600 "$BASE_URL/v1/images/generations" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2.5-flare",
    "prompt": "一只橘猫坐在窗台，午后自然光，摄影质感",
    "size": "1:1",
    "resolution": "1k",
    "n": 1
  }'
```

此示例中的 `size` 是比例字符串，`resolution` 是小写档位。不是所有图片模型都使用这两个字段或接受同样的值。多媒体线路可返回异步任务；原有 OpenAI / Grok 同步图片线路也可能直接返回图片结果，按 HTTP 响应及任务页说明处理。

### 3.2 视频

```bash
curl --fail-with-body --max-time 600 "$BASE_URL/v1/videos/generations" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance-2.0",
    "prompt": "镜头缓慢推进，海边灯塔在日落中亮起，海浪轻拍礁石",
    "size": "16:9",
    "duration": 5,
    "generate_audio": true
  }'
```

提交返回任务后，继续查询 `/v1/media/tasks/{task_id}`，不要把提交成功当成视频已生成。

### 3.3 音乐

```bash
curl --fail-with-body --max-time 600 "$BASE_URL/v1/music/generations" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "suno",
    "version": "v6",
    "custom": false,
    "prompt": "适合旅行短片的轻快原声吉他配乐",
    "instrumental": true
  }'
```

Suno 自定义模式与普通模式的必填项不同；Flowmusic 使用 `sound_prompt` / `lyrics`，不能把上面 Suno 的字段原样套用过去。

## 4. 可选：生成前估价

估价是可选的开发辅助，不是调用生成接口前必须执行的解锁步骤。

```bash
curl --fail-with-body "$BASE_URL/v1/media/quote" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "seedance-2.0",
    "operation": "video",
    "parameters": {
      "prompt": "海边灯塔在日落中亮起",
      "size": "16:9",
      "duration": 5,
      "generate_audio": true
    }
  }'
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model` | 字符串 | 是 | 精确模型 ID |
| `operation` | 字符串 | 是 | 图片 `image`、视频 `video`、音乐 `music`；以密钥目录声明的能力为准 |
| `parameters` | 对象 | 建议 | 本次要生成的实际参数，不是另一份随意精简的配置；省略时按空对象处理，可能无法通过该模型校验 |
| `input_assets` | 数组，按模型契约 | 否 | 采用统一素材协议时传入；模型原生 `image_urls` 等字段仍按该型号文档填写 |

主要响应字段：`estimated_amount`、`estimated_quota_amount`、`currency`、`billing_unit`、`pricing_revision`、`catalog_revision`、`within_key_limits`、`uncertainty`、`warnings`。以响应中的结算单位和估算说明为准，不要在客户端写死价格或把估价当成最终账单。

## 5. 参数说明的阅读顺序

1. **精确模型 ID**：先核对目录是否包含该型号。
2. **任务模式**：文生图、图生图、首尾帧、多模态参考、视频编辑、续写、草稿精修等不是同一套输入。
3. **必填、类型与枚举**：尤其区分布尔值 `true` 与字符串 `"true"`，以及数字 `120` 与字符串 `"120"`。
4. **条件约束**：模式变化可能增加必填项；有些字段互斥，不能全部塞进请求。
5. **公共任务流程**：提交、保存任务 ID、查询、下载结果、处理未知提交状态。

不要把示例里的占位素材地址直接用于生成。替换为自己的可访问 HTTPS 直链，或者使用任务页说明的素材上传接口。不要把 API Key 填到提示词、素材 URL 或分享链接里。

## 6. 模型参数与公共任务文档

- [媒体任务查询、结果下载与请求停止](https://docs-api.onemini.top/9683548m0)
- 具体型号参数见左侧「图片模型 API」「视频模型 API」「音乐模型 API」分类。

### 图片模型（59 个精确型号）

| 调用文档 | 型号数 | 精确模型 ID |
|---|---:|---|
| [GPT Image 基础系列与 DALL·E](https://docs-api.onemini.top/9683552m0) | 7 | `dall-e-3`、`gpt-image-1`、`gpt-image-1-official`、`gpt-image-1.5`、`gpt-image-1.5-official`、`gpt-image-2`、`gpt-image-2-official` |
| [GPT Image 2.5 / 2.6：生成、编辑、GIF 与 SVG](https://docs-api.onemini.top/9683550m0) | 6 | `gpt-image-2.5-ext`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`、`gpt-image-2.6-ext`、`gpt-image-2.6-flare`、`gpt-image-2.6-sunburst` |
| [Gemini 与 Nano Banana 图片模型](https://docs-api.onemini.top/9683551m0) | 10 | `gemini-2.5-flash-image-preview`、`gemini-2.5-flash-image-preview-official`、`gemini-3-pro-image-preview`、`gemini-3-pro-image-preview-official`、`gemini-3.1-flash-image-preview`、`gemini-3.1-flash-image-preview-official`、`gemini-3.1-flash-lite-image`、`gemini-3.1-flash-lite-image-ext`、`gemini-nano-banana-2.1`、`gemini-nano-banana-2.1-ext` |
| [Seedream：文生图、参考图、透明背景与分层](https://docs-api.onemini.top/9683554m0) | 6 | `seedream-4-0`、`seedream-4-5`、`seedream-5-0-flash`、`seedream-5-0-lite`、`seedream-5-0-pro`、`seedream-5-5` |
| [Flux：Kontext、2 系列与 3 系列](https://docs-api.onemini.top/9683553m0) | 7 | `flux-2-flex`、`flux-2-max`、`flux-2-pro`、`flux-3-image`、`flux-3-image-klein`、`flux-kontext-max`、`flux-kontext-pro` |
| [Grok：图片生成、分层与区域编辑](https://docs-api.onemini.top/9683555m0) | 8 | `grok-imagine-1.0-apimart`、`grok-imagine-1.0-edit-apimart`、`grok-imagine-1.5-apimart`、`grok-imagine-1.5-edit-apimart`、`grok-imagine-2.0-ext`、`grok-imagine-image`、`grok-imagine-image-2.0`、`grok-imagine-image-quality` |
| [Wan 2.7 图片系列](https://docs-api.onemini.top/9683556m0) | 2 | `wan2.7-image`、`wan2.7-image-pro` |
| [MiniMax H3 图片系列](https://docs-api.onemini.top/9683558m0) | 2 | `minimax-h3-image`、`minimax-h3-image-highspeed` |
| [Qwen Image 与 Z Image](https://docs-api.onemini.top/9683557m0) | 5 | `qwen-image-2.0`、`qwen-image-2.0-pro`、`qwen-image-3.0`、`qwen-image-3.0-pro`、`z-image-turbo` |
| [Imagen、混元、MAI 与 Muse 图片模型](https://docs-api.onemini.top/9683559m0) | 5 | `hy-image-v3.5-preview`、`imagen-4.0-apimart`、`mai-image-2.6`、`mai-image-2.6-flash`、`muse-image-ext` |
| [Midjourney：模型参数与本站路由边界](https://docs-api.onemini.top/9683560m0) | 1 | `midjourney` |

### 视频模型（61 个精确型号）

| 调用文档 | 型号数 | 精确模型 ID |
|---|---:|---|
| [Seedance 1.0 Pro 视频](https://docs-api.onemini.top/9683561m0) | 2 | `seedance-1-0-pro-fast`、`seedance-1-0-pro-quality` |
| [Seedance 2.0 多模态与 face 变体](https://docs-api.onemini.top/9683562m0) | 5 | `seedance-2.0`、`seedance-2.0-fast`、`seedance-2.0-mini`、`seedance-2.0-face`、`seedance-2.0-fast-face` |
| [Seedance 2.5 多模态、草稿、编辑与续写](https://docs-api.onemini.top/9683563m0) | 1 | `seedance-2.5` |
| [Zoe 多模态视频与草稿转正式](https://docs-api.onemini.top/9683564m0) | 1 | `apimart-zoe` |
| [Veo 3.1 普通版与 official 版](https://docs-api.onemini.top/9683565m0) | 5 | `veo3.1-fast`、`veo3.1-quality`、`veo3.1-lite`、`veo3.1-fast-official`、`veo3.1-quality-official` |
| [Kling 视频、多镜头、动作控制与 V4](https://docs-api.onemini.top/9683566m0) | 9 | `kling-v2-6`、`kling-v3`、`kling-3.0-turbo`、`kling-v3-omni`、`kling-video-o1`、`kling-v2-6-motion-control`、`kling-v3-motion-control`、`kling-v4`、`kling-v4-flash` |
| [Wan 2.5、2.6、2.7 与 3.0 视频](https://docs-api.onemini.top/9683567m0) | 9 | `wan2.5-preview`、`wan2.6`、`wan2.6-i2v`、`wan2.6-i2v-flash`、`wan2.7`、`wan2.7-r2v`、`wan2.7-videoedit`、`wan3.0-video`、`wan3.0-video-prime` |
| [MiniMax Hailuo 与 H3 系列](https://docs-api.onemini.top/9683568m0) | 7 | `MiniMax-Hailuo-02`、`MiniMax-Hailuo-2.3`、`MiniMax-Hailuo-2.3-Fast`、`MiniMax-H3`、`MiniMax-H3-Max`、`MiniMax-H3-Context-IR`、`MiniMax-H3-Regeneration` |
| [Grok 视频：标准型号与独立变体](https://docs-api.onemini.top/9683569m0) | 4 | `grok-imagine-video`、`grok-imagine-video-1.5`、`grok-imagine-1.0-video-apimart`、`grok-imagine-1.5-video-apimart` |
| [Gemini Omni 视频与 Omni-Flash-Ext](https://docs-api.onemini.top/9683570m0) | 5 | `gemini-omni-1.1-flash`、`gemini-omni-1.2-flash`、`gemini-omni-1.2-flash-ext`、`gemini-omni-flash-preview`、`Omni-Flash-Ext` |
| [Flux 3 视频与草稿转正式](https://docs-api.onemini.top/9683571m0) | 1 | `flux-3-video` |
| [HappyHorse 视频与参考视频编辑](https://docs-api.onemini.top/9683572m0) | 2 | `happyhorse-1.0`、`happyhorse-1.1` |
| [SkyReels V4 图片、视频与中间帧参考](https://docs-api.onemini.top/9683573m0) | 2 | `skyreels-v4-fast`、`skyreels-v4-std` |
| [Vidu Q3 与 Q4 视频](https://docs-api.onemini.top/9683574m0) | 5 | `viduq3`、`viduq3-mix`、`viduq3-pro`、`viduq3-turbo`、`viduq4-preview` |
| [PixVerse V6、Max 与续写](https://docs-api.onemini.top/9683575m0) | 2 | `pixverse-v6`、`pixverse-v6-max` |
| [Muse Video Ext 视频](https://docs-api.onemini.top/9683576m0) | 1 | `muse-video-ext` |

### 音乐模型（2 个精确型号）

| 调用文档 | 型号数 | 精确模型 ID |
|---|---:|---|
| [Suno 音乐生成与角色风格](https://docs-api.onemini.top/9683577m0) | 1 | `suno` |
| [Flowmusic 音乐生成](https://docs-api.onemini.top/9683578m0) | 1 | `flowmusic` |

