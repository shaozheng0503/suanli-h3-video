# 示例 1：一句话出一条片

```bash
python3 scripts/h3_batch.py submit \
  --comfy https://deployment-452-xxxx-8188.550w.link \
  --prompt "雨夜霓虹街头，一只机械狗叼着玫瑰花穿过斑马线，缓慢跟拍，赛博朋克氛围。Audio: 雨声、远处车流、低沉合成器" \
  --resolution 768p --duration 5
```

约 81 秒出片（热态），产出 `jobs/<ts>/raw/task001_seed<随机>.mp4`（自带音轨）。

# 示例 2：批量 8 条（同 prompt 多 seed）

```bash
python3 scripts/h3_batch.py submit \
  --comfy $COMFY \
  --prompt "航拍视角掠过清晨的梯田，雾气缭绕，阳光逐渐洒满山谷。Audio: 风声与鸟鸣" \
  --count 8 --resolution 768p --duration 5
```

每条随机 seed（防执行缓存），任务间隔 8 秒（防单卡 RAM 累积 OOM）。约 12 分钟全部完成。

# 示例 3：批量多 prompt

```bash
# tasks.txt 每行一个 prompt
python3 scripts/h3_batch.py submit --comfy $COMFY --prompt-file tasks.txt --resolution 768p --duration 5
```

# 示例 3.5：首帧图生视频（I2V）

```bash
python3 scripts/h3_batch.py submit \
  --comfy $COMFY --mode i2v \
  --first-frame start_frame.png \
  --prompt "The woman starts her aerobics routine, energetic jumps, gym lighting. Audio: upbeat workout music" \
  --resolution 768p --duration 5
```

一张首帧图驱动整个视频的起点（`--first-frame`）；图的长宽比尽量与生成分辨率一致。实测 768p/5s 含上传约 112s。

# 示例 4：参考图生视频（R2V）

```bash
python3 scripts/h3_batch.py submit \
  --comfy $COMFY --mode r2v \
  --prompt "<Picture 1> 中的角色在暴雨中拔剑回身，电影感打光，慢动作。Audio: 雨声与剑鸣" \
  --ref-images hero_ref.png \
  --resolution 768p --duration 5
```

注意：提示词必须用 `<Picture 1>` 引用参考图；多图按接入顺序编号。
默认 `ref_image_size=match`（参考图缩放到生成分辨率，快）；身份保真要求高时在 api 文档查 `max` 模式。

# 示例 5：生成批完成后统一超分 2K

```bash
python3 scripts/h3_batch.py upscale \
  --vsr https://deployment-452-xxxx-8001.550w.link \
  --job jobs/20261029_1100/ --scale 2.0 --quality HIGH
```

产物在 `jobs/<ts>/2k/*_2k.mp4`。**必须等生成批全部 ok 后执行**（同卡显存叠加会 OOM）。

# 示例 6：查状态、断点续跑、失败重试与收尾

```bash
# 状态（含错误分类：done / retryable / fatal）
python3 scripts/h3_batch.py status --job jobs/20261029_1100/
python3 scripts/h3_batch.py status --job jobs/20261029_1100/ --json   # Agent 解析用

# 断点续跑：批量中断后接着跑（已完成自动跳过）
python3 scripts/h3_batch.py submit --comfy $COMFY --job jobs/20261029_1100/ \
  --prompt-file tasks.txt --count 8 --resolution 768p --duration 5

# 只重试失败任务（timeout/网络错误；i2v/r2v 失败需带图 submit --job 重交）
python3 scripts/h3_batch.py retry --comfy $COMFY --job jobs/20261029_1100/ --only task003

# 收尾：manifest + 自动暂停实例（需 SUANLI_TOKEN 环境变量）
python3 scripts/h3_batch.py done --job jobs/20261029_1100/ --task-id 3571477
```
