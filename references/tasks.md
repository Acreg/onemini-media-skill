# 媒体任务查询、结果下载与请求停止

# 媒体任务查询、结果下载与请求停止

本文是图片、视频和音乐模型共用的任务处理说明。示例假定已设置：

```bash
export BASE_URL="https://api.onemini.top"
export API_KEY="替换成该环境的 API Key"
```

## 1. 先分清同步结果和异步任务

多媒体适配线路通常返回 HTTP `202` 和 `data` 任务对象。下列为**字段结构示意，不是实际生成记录**，省略了与模型有关的附加字段：

```json
{
  "data": {
    "task_id": "<本站返回的任务编号>",
    "id": "<同一个任务编号>",
    "object": "media.task",
    "model": "<请求使用的精确模型 ID>",
    "kind": "video",
    "operation": "video",
    "status": "queued",
    "progress": 0,
    "created_at": 0,
    "updated_at": 0,
    "billed": false
  }
}
```

- 保存 `data.task_id`（或同值 `data.id`）；响应头若有 `X-Media-Task-ID`，也一并保存。
- 这里的 `task_id` 是 **本站任务编号**，不应替换成上游任务编号，也不要自行拼接编号。
- HTTP `202` 只表示任务已受理或状态已保存，不表示生成成功；还要读取 `status` 和 `error`。
- 原有同步图片线路可直接返回图片结果，如 `data[].url` 或 `data[].b64_json`。数组结果不是上面的任务对象，不需要凭空造一个任务编号去轮询。
- 本页统一任务路由不等于原生 Grok 视频任务路由或旧版 `/v1/images/tasks/{task_id}`。使用哪套提交接口，就按该接口返回的任务协议查询，不混用。

## 2. 查询单个任务

```bash
export TASK_ID="替换成提交返回的本站任务编号"
curl --fail-with-body "$BASE_URL/v1/media/tasks/$TASK_ID" \
  -H "Authorization: Bearer $API_KEY"
```

返回 `data` 中为最新任务对象。

| `status` | 中文含义 | 客户端怎么做 |
|---|---|---|
| `submitting` | 正在提交 | 保留原任务，继续查询，不重复提交 |
| `queued` | 排队中 | 继续查询 |
| `running` | 生成中 | 继续查询 |
| `succeeded` | 生成完成 | 读取 `result`；如 `billed=false`，结算可能仍在核对，不要因此重新生成 |
| `failed` | 任务失败 | 读取 `error`，按真实错误调整；不要把失败当成空成功 |
| `submit_unknown` | 提交结果待核对 | 不确定是否已发送到模型服务；保留原标识核对，不要自动新建另一单 |
| `cancelled` | 已停止 | 结合停止接口的确认字段判断是否已确认未外发 |

建议每隔数秒查询一次，完成或失败后停止轮询；本地轮询时间到了只代表客户端结束等待，不代表服务端停止生成。

`progress` 仅作展示，不保证每个模型都持续更新。`created_at` / `updated_at` 为 Unix 秒时间戳。`result`、`error`、计费字段和媒体规格字段按当前状态及模型按需出现。

## 3. 下载生成结果

任务成功后读取 `data.result` 中实际返回的图片、视频或音乐结果。不同模型可能返回多个产物、封面、歌词或附加信息，不能把所有任务的结果都硬编码成单一固定路径。

常见视频结果包含 `result.videos`；图片结果可能是图片集合、URL 或 Base64；音乐结果按 Suno / Flowmusic 页说明读取实际音频链接。

若拿到公开或签名下载 URL：

```bash
export RESULT_URL="替换成任务结果中的实际下载地址"
curl --fail --location "$RESULT_URL" --output result.mp4
```

图片或音乐分别把文件名换成对应的 `.png` / `.jpg` / `.webp` / `.gif` / `.svg` / `.mp3` / `.wav`，以真实 MIME 类型和模型输出格式为准。**下载第三方结果链接时不要附带 Onemini 的 Authorization 请求头。** 对象或签名链接可能过期，取得结果后及时保存。

`GET /v1/media/tasks/{task_id}/content` 当前用于站内存储的语音合成 / 音频对话内容，**不是 Suno / Flowmusic 音乐文件或通用视频下载接口**；不要用它替代音乐任务返回的音频 URL。

## 4. 按业务标记找回任务

支持的任务提交请求可以传 `client_ref` 作为自定义业务标记。`client_ref` 用于找回，不等于幂等键。

```bash
curl --fail-with-body --get "$BASE_URL/v1/media/tasks" \
  -H "Authorization: Bearer $API_KEY" \
  --data-urlencode 'client_ref=order-20261115-001' \
  --data-urlencode 'kind=video' \
  --data-urlencode 'operation=video' \
  --data-urlencode 'limit=20'
```

| 查询参数 | 类型 | 说明 |
|---|---|---|
| `client_ref` | 字符串 | 按提交时的业务标记筛选 |
| `kind` | 字符串 | 媒体类型，例如 `image`、`video`、`audio` |
| `operation` | 字符串 | 操作，例如 `image`、`video`、`music` |
| `limit` | 整数 | 1–100；未传或不在范围内使用 50 |

普通任务列表返回 `{"object":"list","data":[...]}`。同一用户的不同 Key、不同环境任务不能相互冒用。

## 5. 需要来源任务的续写、编辑与草稿精修

部分型号需要 `source_task_id`、`draft_task_id`、`extend_from_task_id` 等字段。不要用下载 URL、上游原始编号或另一个 Key 的编号代替。

```bash
curl --fail-with-body --get "$BASE_URL/v1/media/tasks" \
  -H "Authorization: Bearer $API_KEY" \
  --data-urlencode 'source_model=seedance-2.5' \
  --data-urlencode 'source_field=draft_task_id' \
  --data-urlencode 'page=1' \
  --data-urlencode 'limit=50'
```

从 `data[].source_reference.value` 取该字段应提交的值。该接口按目标型号和目标字段筛选本人、当前 Key、兼容状态与线路的来源任务，返回 `source_task_contract`、`page`、`limit`、`has_more`。

若 `has_more=true`，继续下一页，即使某一页 `data` 为空。草稿期限、是否必须同型号、可复用的素材类型等限制依目标模型页为准；不能推断「同一家族都通用」。

## 6. 本地图片 / 视频上传为参考素材

如果已有模型可读取的 HTTPS 素材直链，可以直接使用，不要求先上传。需要从本地文件取得素材 URL 时：

```bash
curl --fail-with-body \
  "$BASE_URL/v1/media/uploads/images?model=seedance-2.0&retention_days=3" \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@reference.jpg"
```

| 项目 | 说明 |
|---|---|
| 图片上传 | `POST /v1/media/uploads/images`，单文件最大 30 MiB |
| 视频上传 | `POST /v1/media/uploads/videos`，单文件最大 50 MiB |
| `model` | 必填查询参数，目标型号必须在当前 Key 的可用范围内并支持该上传类型 |
| `retention_days` | 可选查询参数，`3`、`7`、`15`、`30`，默认 `3` |
| 请求体 | `multipart/form-data`，恰好一个名为 `file` 的非空文件；不要手写 boundary |
| 响应 | 顶层 `url`，以及可能返回的文件名、类型、字节数、过期时间等；不是 `data.url` |

将返回的 `url` 填入该型号要求的 `image_urls`、`image_url`、`first_frame_image`、`video_urls` 等对应字段。字段名称与数量必须按模型页，不是任意互换。

此统一上传接口当前只接受 `images` / `videos`，**没有 `/v1/media/uploads/audios`**。音乐参考音频、模型自带上传动作或其他音频输入，按相应模型的已支持入口处理。

## 7. 幂等与重试

可使用 `Idempotency-Key` 标记一次生成。一个标识只能对应同一笔请求，不要在更改提示词或参数后复用它；冲突可能返回 HTTP `409`。`client_ref` 只是业务检索标记，不能代替幂等键。

网络断开、HTTP 超时或 `submit_unknown` 并不证明请求没有提交。先通过 `X-Media-Task-ID`、原任务编号或 `client_ref` 查询原任务，再决定下一步。不要通过更换幂等键自动重复生成。

## 8. 可选：在外发前停止请求

这一协议用于目录声明支持 `request_cancellation` 的**创作密钥媒体请求**，不是所有普通文本密钥或原生任务接口都支持。正常生成不要求先调用准备接口；只有需要跨请求确认「未发送模型服务」时才采用以下流程。

### 第一步：生成前领取请求身份

```bash
curl --fail-with-body --request POST "$BASE_URL/v1/media/requests/prepare" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

保存 `data.idempotency_key`，下面原生成与停止请求都使用这个相同值：

```bash
export REQUEST_IDENTITY="替换成 data.idempotency_key"
curl --fail-with-body "$BASE_URL/v1/videos/generations" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Idempotency-Key: $REQUEST_IDENTITY" \
  -H "Content-Type: application/json" \
  -d '{"model":"seedance-2.0","prompt":"海边日落","duration":5,"size":"16:9"}'
```

### 第二步：需要停止时发出明确命令

```bash
curl --fail-with-body "$BASE_URL/v1/media/requests/cancel" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Idempotency-Key: $REQUEST_IDENTITY" \
  -H "Content-Type: application/json" \
  -d '{"request_path":"/v1/videos/generations"}'
```

`request_path` 是原生成请求的准确路径，不是完整网址，也不是查询任务的路径。图片、音乐分别使用原请求实际的 `/v1/images/generations` 或 `/v1/music/generations` 等受支持路径。

| 响应 `data.state` | 真实含义 |
|---|---|
| `cancelled_before_dispatch` | 已确认未外发；同时检查 `confirmed_not_dispatched=true` |
| `dispatch_started` | 已进入派发，无法确认没发送；核对原任务 |
| `already_submitted` | 已提交，停止请求不能撤回模型生成 |
| `unknown` | 状态未确认，不能当成停止成功 |

关闭网页、取消本地等待、停止轮询都不能代替停止 API，也不能撤销已发生的模型调用。

## 9. 常见错误

| HTTP 状态 | 优先检查 |
|---|---|
| `400` / `422` | 精确模型 ID、参数类型、条件必填、互斥项、素材与来源任务、报价是否可用；读取 `error.message` 和可能的 `error.details` |
| `401` / `403` | Key 是否有效、是否属于当前环境、分组与模型权限 |
| `404` | 用错环境、任务不属于该 Key、查询协议混用、内容下载接口与媒体类型不匹配 |
| `409` | 幂等标识对应了不同请求，或原请求已经进入处理；先查询原单 |
| `429` | 并发或速率限制；尊重响应给出的等待时间 |
| `502` / `503` | 目录、线路、任务存储或模型服务暂不可用；若已取得任务编号，先查原单，避免重复生成 |

生成会使用相应模型的额度或余额。具体收费以当前环境、当前 Key 的报价和最终账单为准。

