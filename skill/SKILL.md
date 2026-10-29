---
name: suanli-h3-video
description: >-
  在共绩算力(suanli.cn)平台上用一句话批量生成 MiniMax H3 视频：自动租 5090 显卡、
  启动 h3vsr-merge 镜像（H3 生成 8188 + RTX VSR 超分 8001）、提交批量任务、跟踪进度、
  下载成片、用完自动暂停实例。支持文生视频(T2V)与参考生视频(R2V)，768p 生成可一键
  超分到 2K。在用户提到 H3、生成视频、批量出片、文生视频、参考生视频、超分、
  "一句话一部片"、租卡跑视频时启用。
---

# suanli-h3-video：一句话，一部片（MiniMax H3 × 共绩算力）

把一句话想法变成带音频的成片。Agent 负责：租卡 → 起服务 → 批量生成 →（可选）2K 超分 → 下载 → 关机省钱。

## 前置

向用户确认凭证（平台：右上角头像 → API 密钥）。模板见 [`.env.example`](.env.example)。

| 变量 | 说明 |
|------|------|
| `SUANLI_TOKEN` | 共绩 Open API 密钥（**简易模式**，无需加签）|
| `H3_RESOURCE_MARK` | 可选。5090 资源 mark；留空则部署前自动查询当前有库存的 mark |

公共 Header（所有 Open API 请求）与计费换算见 [api/README.md](api/README.md)。

## 能力总览

| 想做什么 | 用法 |
|---|---|
| 一句话出一条片 | `生成视频：雨夜霓虹街头，一只机械狗叼着玫瑰穿过斑马线` |
| 批量出片 | `用这个提示词生成 8 条，换不同 seed` |
| 首帧图生视频 | `从这张图开始生成：她开始跳操`（I2V 模式，`--first-frame`）|
| 参考图生视频 | `按这张图的角色生成打斗视频`（R2V 模式，`<Picture 1>` 引用）|
| 768p → 2K | `生成后超分到 2K`（VSR 链路自动串行）|
| 查任务/关机 | `查一下我的 H3 任务` / `跑完了把实例停掉` |

## 核心约束（Agent 必读）

1. **镜像**：`harbor.suanleme.cn/public-hub/h3vsr-merge:0.1.4-5090`，端口 8188（ComfyUI 生成）+ 8001（VSR 超分）
2. **帧数网格**：`length` 必须满足 `(length-5) % 17 == 0` 且 `124 ≤ length ≤ 362`；5s=124 / 10s=243 / 15s=362。**非法帧数不报错，会静默取整**（245→260）
3. **分辨率**：宽高必须 32 的倍数。推荐 1344×768（768p）；1080p 在 32G 卡上跑 10s/15s 会 OOM
4. **seed 防缓存**：批量每条必须随机 seed，否则同参数命中 ComfyUI 执行缓存秒回旧结果
5. **显存串行**：同卡 both 模式下生成(27.8G)与超分(10.5G)叠加必 OOM——**先生成完一批，再统一超分**。批量生成内部为滑动窗口（同时在途 2 条，ComfyUI 侧自动排队；126GB 实测安全，6% 提速）
6. **编码器**：VSR 输出在共绩平台必须 `vcodec=libx264`（节点 NVENC 不可用）
7. **内存档位**：下单时确认宿主 memory ≥128GB（58GB 廉价档跑批量会 OOMKilled）
8. **冷启动**：实例创建后 ComfyUI 1~3 分钟内可能拒绝连接，脚本已内置重试

## 标准流程（Agent 按 5 步走）

### ① 部署实例（如果用户没有在跑的）

用 [api/deployment.md](api/deployment.md) 的 `task/create`：镜像填 h3vsr-merge、端口 8188+8001、资源选 5090（先查库存拿 mark）。返回 `task_id` 与 `deployment-452-xxxx-{8188,8001}.550w.link` 域名。
然后 `GET {8188域名}/system_stats` 探活（脚本自动重试到 200）。

### ② 组装工作流并提交

调 [`scripts/h3_batch.py`](scripts/h3_batch.py)（纯标准库，无第三方依赖）：

```bash
# 单条：一句话 → 成片
python3 scripts/h3_batch.py submit \
  --comfy https://deployment-452-xxxx-8188.550w.link \
  --prompt "雨夜霓虹街头，一只机械狗叼着玫瑰花穿过斑马线，缓慢跟拍，赛博朋克。Audio: 雨声与电子合成器氛围" \
  --resolution 768p --duration 5 --seed auto

# 批量：同 prompt 多 seed
python3 scripts/h3_batch.py submit --comfy $COMFY \
  --prompt-file tasks.txt --count 8 --resolution 768p --duration 5

# R2V：参考图
python3 scripts/h3_batch.py submit --comfy $COMFY --mode r2v \
  --prompt "<Picture 1> 中的角色在雨中拔剑，电影感" --ref-images hero.png
```

参数规范见 [api/h3-generation.md](api/h3-generation.md)（T2V/R2V 工作流差异、参数约束、易踩坑）。

### ③ 跟踪进度（含错误分类）

```bash
python3 scripts/h3_batch.py status --job jobs/20261029_1100/
#   task001 ok         [done]      1.7MB 2K✓
#   task003 timeout    [retryable]
#   task005 error: ... [fatal]
python3 scripts/h3_batch.py status --job jobs/20261029_1100/ --json   # Agent 解析用
```

**断点续跑**：中断后 `submit --job jobs/<ts>/ ...` 重跑，已完成任务自动跳过。
**失败重试**：`retry --comfy $COMFY --job jobs/<ts>/ [--only task003]` 只重跑可重试项（timeout/网络/comfy 错误；node_errors 参数错属 fatal 不重试）。注意 i2v/r2v 的失败任务要用 `submit --job` 带图重交（实例重启会清空已上传图片）。

### ④ 可选：2K 超分

```bash
python3 scripts/h3_batch.py upscale --vsr https://deployment-452-xxxx-8001.550w.link \
  --job jobs/20261029_1100/ --scale 2.0 --quality HIGH
```

先生成批全部完成才执行（显存串行约束）；参数见 [api/vsr-upscale.md](api/vsr-upscale.md)。

### ⑤ 收尾（关键：省钱）

```bash
# 生成 manifest 并直接暂停实例（SUANLI_TOKEN 从环境变量读）
python3 scripts/h3_batch.py done --job jobs/20261029_1100/ --task-id 3571477
```

**用户没明确说继续用时，全部任务终态后主动建议暂停实例**——共绩按秒计费，挂着就烧钱。

## 计费速查（5090 单卡）

| 产物 | 耗时 | 成本参考 |
|---|---|---|
| 768p / 5s | ~81s 热态 | ~¥0.08 |
| 768p / 5s + 2K 超分 | +~10s (HIGH) | ~¥0.09 |
| 768p / 10s | ~243s | ~¥0.22 |

卡时价按实例档位计（约 ¥3.25/时 × 实际秒数）。计费换算与出账周期见 [api/README.md](api/README.md)。

## 错误处理速查

| 症状 | 原因 | 处理 |
|---|---|---|
| 提交返回 node_errors 非空 | 工作流参数错 | 看 `node_errors` 详情，多为帧数/分辨率非法 |
| 轮询一直无结果 | 实例重启清了队列 | 用 status 查，缺的重跑 submit（断点续跑）|
| `/prompt` 秒回但无视频 | 执行缓存命中 | 确认每条随机 seed |
| VSR 轮询到 error | quality 非法（异步暴露） | 检查 quality 枚举值 |
| 容器 OOMKilled (Exit 137) | 内存档位太小 / 同卡并行 | 换 ≥128GB 档；严格串行 |

## 文档地图

| 文件 | 内容 |
|---|---|
| [api/README.md](api/README.md) | Open API 公共规范（Header/计费/响应格式）|
| [api/deployment.md](api/deployment.md) | 实例创建/探活/暂停（task/create、system_stats、task/pause）|
| [api/h3-generation.md](api/h3-generation.md) | H3 生成：工作流 JSON、T2V/R2V 差异、参数约束、易踩坑 |
| [api/vsr-upscale.md](api/vsr-upscale.md) | VSR 超分：提交/轮询/下载、参数枚举、并发约束 |
| [scripts/h3_batch.py](scripts/h3_batch.py) | 批量执行器（提交/状态/超分/收尾，纯标准库）|
| [examples/](examples/) | 示例：单条出片、批量 8 条、R2V 参考生成 |
| [../docs/使用指南.md](../docs/使用指南.md) | 面向 C 端用户的使用说明 |
