# VSR 视频超分（FastAPI 8001）

768p 生成结果 → 2K 的 AI 超分链路（RTX VSR / nvidia-vfx）。**只在生成批全部完成后执行**（同卡显存叠加约束）。

## 接口

```
GET  {vsr}/healthz                          # 探活：status/concurrency/queue_visible
POST {vsr}/api/v1/upscale/file              # multipart 提交
GET  {vsr}/api/v1/tasks/{id}                # 轮询状态
GET  {vsr}/api/v1/tasks/{id}/result         # 下载（mp4 二进制流，非 JSON）
```

完整 Swagger：`{vsr}/docs`。

## 提交参数

```bash
curl -X POST {vsr}/api/v1/upscale/file \
  -F "file=@input.mp4" \
  -F "scale=2.0" \
  -F "quality=HIGH" \
  -F "vcodec=libx264" \
  -F "bitrate=50M"
```

| 参数 | 说明 |
|---|---|
| `file` | 输入视频（mp4/h264 最佳；**带音轨原样保留**）|
| `scale` | 放大倍数，支持小数（1.5 可用）；768p→2K 用 2.0 |
| `quality` | `BICUBIC`（纯插值自检）/ `LOW` / `MEDIUM` / **`HIGH`（AI 生成源推荐）** / `ULTRA`（与 HIGH 差距小）/ `HIGHBITRATE_*`（高码率源）|
| `vcodec` | **共绩平台必须 `libx264`**（节点 NVENC 不可用）；自建机可用 `h264_nvenc` |
| `bitrate` | 2K 建议 `50M` |

## 轮询与下载

- 提交响应：`{"task_id":"...","status":"queued","poll":"/api/v1/tasks/..."}`
- 状态流转：`queued → running → done | error`（`progress` 0~1）
- done 后 `GET .../result` 直接下载 mp4（Content-Disposition 头），**不要按 JSON 解析**

## 错误码

| 场景 | 表现 |
|---|---|
| scale/缺 file 非法 | 422 + detail |
| quality 非法 | **提交返回 200，轮询时才见 error**（异步校验）|
| task_id 不存在 | 404 |

## 并发与耗时

- `concurrency.max=3`，但**同卡 both 模式实际安全并发 = 1**（且仅在生成空闲时）
- `queue_visible` 只是"已提交未开始"数，不是队列深度
- HIGH 档约 10.1s/条（5s 片）；软编忙时可达 100s
