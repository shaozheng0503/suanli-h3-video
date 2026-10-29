# H3 视频生成（ComfyUI 8188）

## 提交与轮询

```
POST {comfy}/prompt        body: {"prompt": <workflow JSON>, "client_id": "h3-skill"}
→ {"prompt_id": "...", "node_errors": {}}   # node_errors 非空 = 未入队，看详情
GET  {comfy}/history/{prompt_id}
→ status.status_str: "success" | "error"；outputs 里有 filename/subfolder
GET  {comfy}/view?filename=...&subfolder=...&type=output   # 下载 mp4
```

上传图片（I2V 首帧 / R2V 参考）：`POST {comfy}/upload/image`（multipart，字段 `image`，**客户端超时 ≥600s**）。

## 三种生成模式（按模式切换的节点）

| 节点 | T2V（文生） | I2V（图生） | R2V（参考生） |
|---|---|---|---|
| UNETLoader.unet_name | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 同 T2V（fl2va） | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| LoraLoaderModelOnly.lora_name | `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` | 同 T2V | `minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` |
| 核心节点 class_type | `MiniMaxH3ImageToVideo`（不接图 = 纯文生） | `MiniMaxH3ImageToVideo` + `first_frame: ["201", 0]` | `MiniMaxH3ReferenceToVideo` |
| 额外必填 | — | LoadImage 节点（201） | `audio_vae: ["15", 0]`、`ref_images: [["201",0], ...]`（**列表**）、`ref_image_size: "match"\|"max"` |

- **I2V**：一张首帧图驱动画面与运镜；输入图长宽比尽量与生成分辨率一致（否则裁切/拉伸）
- **R2V**：最多 9 张参考图；提示词用 `<Picture 1>`、`<Picture 2>` 引用（序号 = LoadImage 接入顺序）；`ref_image_size` 选 `match`（缩放到生成分辨率，快）或 `max`（保留 2048px 短边，身份保真强但慢）

## 采样链（勿动）

`RandomNoise → BasicGuider → KSamplerSelect(euler) → BasicScheduler(simple, steps=4, denoise=1.0) → SamplerCustomAdvanced`；
CFG=1.0（turbo 要求）；`MiniMaxH3SigmaShift(shift_video=12.0, shift_audio=3.0)`；fps=24。

**注意**：`BasicScheduler` 必须带 `denoise: 1.0`（缺了会 400 required_input_missing）。

## 参数约束

| 参数 | 约束 |
|---|---|
| width × height | 32 的倍数。480p=864×480 / 720p=1280×720 / **768p=1344×768（推荐）** / 1080p=1920×1080（32G 卡 10s/15s 会 OOM）|
| length | `(length-5) % 17 == 0` 且 `124 ≤ length ≤ 362`：5s=124 / 10s=243 / 15s=362。**非法值静默取整**（245→260，多耗时且降质）|
| prompt | 画面 + 音轨都写（"Audio: ..."），H3 同步生成音频 |
| seed | **批量每条必须随机**（防执行缓存秒回旧结果）；复现实验时固定 |

## 性能参考（5090 单卡 turbo 4 步，实测）

| 模式 | 768p / 5s 实测 |
|---|---|
| T2V | 146s（含首条模型冷加载 ~60s；热态 ~85s）|
| I2V | 112s（含上传首帧）|
| R2V | 122s（ref_image_size=match，1 张参考图）|
| VSR 2K 超分 | 10.3s（HIGH 档）|

产物规格实测：768p 生成 1344×768/124 帧/aac 音轨 → 2K 超分 2688×1536（aac 音轨原样保留）。

## 易踩坑

1. 产物帧数 = 请求 +3（H3 帧数补偿），不是 bug
2. fl2va ↔ ref2va 切换有 ~124s 冷加载，批量按模型分组跑
3. `/prompt` 无 `/health`；冷启动期连接拒绝需重试
4. 实例重启清空 history/队列——断点续跑靠本地 job.json 记账
5. 同卡 both 模式：生成中提交超分必 OOM（27.8G + 10.5G > 32G），**严格串行**
6. VSR 下载大文件（2K 50MB）可能读超时——提交后轮询 done 再下载，`urlretrieve` 超时设 ≥600s
