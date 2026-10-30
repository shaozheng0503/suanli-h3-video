# suanli-h3-video

**一句话，一部片** · [MiniMax H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) × [共绩算力](https://suanli.cn) × Agent Skill

把开源音画一体视频模型 MiniMax H3 封装为共绩算力平台的 Agent Skill：你说一句话，Agent 自动租 5090 显卡、启动 [`h3vsr-merge`](https://docs.suanli.cn/minimax-h3-turbo-rtx-vsr) 镜像（H3 生成 8188 + RTX VSR 超分 8001）、批量生成视频、下载成片、用完即停。**768p / 5 秒成片约 ¥0.08，2K 约 ¥0.09**。

- 遵循 [suanli.cn Agent Skills 规范](https://docs.suanli.cn/skills)，与官方 [gongji-skills](https://github.com/suanleme/gongji-skills) 同构
- 实现组织参考 [open-source-video-gen-skill](https://github.com/mini-yifan/open-source-video-gen-skill)（Vibe Video）
- 四种模式（文生 / 图生 / 参考生 / 2K 超分）全部真机验证，30 项回归测试

## 为什么是它

- **便宜到可以随便试。** 开源模型跑在自己租的 GPU 上，成本只有 GPU 租金；Agent 管理实例的启停，任务来了才点亮，全部结束立刻熄灭。768p / 5 秒约 ¥0.08——「先试试这个想法」不再是一个需要犹豫的决定。
- **零部署门槛。** 不用下载模型、不用配环境：共绩预制镜像自带全套 H3 权重与 turbo LoRA，Agent 一条 Open API 请求即可拉起服务。
- **音画一体。** H3 原生生成带音频的视频——写画面也写声音（`Audio: ...`），对白、环境音、配乐一次出片，不需要后期配音。
- **断了能续。** 批量任务全程记账（job.json），实例重启、网络中断、进程被杀，重跑一条命令自动跳过已完成项。

## 适用场景

| 场景 | 怎么用 | 示例开场白 |
|---|---|---|
| **灵感速验** | 单条 T2V 直出，5 秒 ¥0.08 | > 生成一条 5 秒视频：日出时热气球升起的卡帕多奇亚。Audio: 晨风与鸟鸣 |
| **自媒体素材批量** | 同 prompt 多 seed 挑最好的一条 | > 这个提示词出 6 条，768p，我挑一条发抖音 |
| **多平台矩阵** | 多 prompt 文件批量 + 2K 超分 | > 按这 10 条文案各出一条竖屏视频，全部超分 2K |
| **角色/IP 内容** | R2V 参考图锁定形象 | > 用这张角色设定图生成雨中撑伞漫步的竖屏视频 |
| **动态首图/封面** | I2V 从静态图起片 | > 让这张海报动起来：镜头缓慢推近，粒子飘散 |
| **电商产品展示** | I2V/R2V + 2K 交付 | > 用产品白底图生成 360° 展示旋转视频，超分 2K |
| **工作室量产** | 批量 + 断点续跑 + manifest 审计 | > 按清单跑 50 条，中断了记得续跑，跑完给我 manifest |
| **A/B 创意测试** | 同 prompt 不同 seed / 多 prompt 对照 | > 这句提示词出 4 条 + 另一句出 4 条，对比哪组画面更好 |

## 安装

```
npx skills add <本仓库地址> --skill suanli-h3-video
```

装好后复制凭证模板并填入密钥（不要提交 `.env`）：

```
cp suanli-h3-video/skill/.env.example suanli-h3-video/skill/.env
```

| 变量 | 说明 |
|------|------|
| `SUANLI_TOKEN` | 共绩 API 密钥（[console.suanli.cn](https://console.suanli.cn) → 右上角头像 → API 密钥，**推荐简易模式**，无需加签）|
| `H3_RESOURCE_MARK` | 可选。5090 资源 mark；留空则部署前自动查询库存 |

**环境要求**：Python 3.8+（`h3_batch.py` 纯标准库，零第三方依赖）、curl。生成费用由共绩平台按秒计费收取。

**确认已接通**：对 Agent 说「用 suanli-h3-video 查一下资源库存」——能返回 5090 列表即凭证就绪。

## 快速开始

### 十秒级单条：不需要任何流程

对 Agent 说：

> 用 suanli-h3-video 生成一条 768p 5 秒视频：雨夜霓虹街头，一只机械狗叼着玫瑰花穿过斑马线，缓慢跟拍，赛博朋克。Audio: 雨声与低沉合成器

约 90 秒出片（含冷启动），产物自带音轨。

### 成批的活：批量 + 超分 + 关机

> 用 suanli-h3-video 批量生成 8 条 768p 5 秒视频，提示词文件是 tasks.txt。
> 全部完成后超分 2K，然后关机。

Agent 执行链：查库存租卡（核内存档位 ≥128GB）→ 起服务 → 批量生成（随机 seed 防缓存，滑动窗口）→ VSR 批量超分 → 下载 → 生成 manifest → 暂停实例停止计费。

### 有参考图：形象锁定

> 用 suanli-h3-video 的参考生模式，按这张角色图生成打斗视频：
> \<Picture 1\> 中的角色在暴雨中拔剑回身，电影感打光。Audio: 雨声与剑鸣

更多示例见 [skill/examples/](skill/examples/README.md)（单条/批量/多 prompt/I2V/R2V/超分/收尾全流程）。

## 能力总览

| 模式 | 入口 | 说明 |
|---|---|---|
| 文生视频 T2V | `--prompt` | 一句话描述画面+声音 → 带音频成片 |
| 图生视频 I2V | `--mode i2v --first-frame 图` | 静态图作为首帧驱动运镜与动态 |
| 参考生视频 R2V | `--mode r2v --ref-images 图...` | 最多 9 张参考图（`<Picture N>` 引用）锁定角色/产品 |
| 2K 超分 | `upscale --scale 2.0 --quality HIGH` | 768p → 2688×1536（RTX VSR AI 超分，音轨保留） |
| 批量 | `--count N` / `--prompt-file f` | 多 prompt / 同 prompt 多 seed；断点续跑；失败分类重试 |

## 成本速查（5090 单卡实测）

| 产物 | 耗时 | 成本 |
|---|---|---|
| 768p / 5s | ~85s 热态 | ~¥0.08 |
| 768p / 10s | ~243s | ~¥0.22 |
| 768p / 5s + 2K 超分 | +10s | ~¥0.09 |
| 冷启动（每次开实例） | 1~3 分钟 | ~¥0.10 |

实例挂着就按秒计费——Agent 收尾时会自动暂停（`done --task-id`）。

## 仓库结构

```
suanli-h3-video/
  skill/                  ← 技能本体（npx skills 安装单位）
    SKILL.md              Agent 入口：能力、8 条硬约束、5 步标准流程
    .env.example          凭证模板
    api/                  接口文档（Agent 按需检索）
      README.md           共绩 Open API 公共规范（Header/计费换算/资源查询）
      deployment.md       实例创建/探活/暂停（含内存档位检查）
      h3-generation.md    H3 生成：三模式工作流、参数约束、易踩坑
      vsr-upscale.md      VSR 超分：提交/轮询/下载、参数枚举
    scripts/
      h3_batch.py         批量执行器（submit/status/retry/upscale/done）
    tests/
      test_h3_batch.py    30 项回归测试（python tests/test_h3_batch.py）
    examples/
      README.md           7 个可复制示例
  docs/
    使用指南.md            面向 C 端用户（提示词写法/FAQ/省钱技巧）
```

## 关键设计决策

1. **纯标准库**：`h3_batch.py` 零第三方依赖——任意 Agent 运行时（Cursor/Claude Code/ZCode）直接可用
2. **断点续跑**：`submit --job` 复用账本，已完成任务自动跳过；中断续跑只补缺
3. **失败分类**：`status` 输出 done/retryable/fatal 三类；`retry` 只重试可重试项（参数错误不浪费时间）
4. **原子下载**：`.part` 临时文件 + 原子改名——半截文件永远不会被当成成功产物
5. **显存串行**：生成与超分严格分批（27.8G + 10.5G > 32G 必 OOM）；批量内部滑动窗口（在途 2 条，实测安全）
6. **防缓存**：批量强制随机 seed（ComfyUI 执行缓存会秒回旧结果）
7. **RAM 喘息**：任务间隔 8s（单 5090 长队列会累积宿主内存导致 OOMKilled）
8. **内存档位检查**：部署文档强制 memory ≥128GB（58GB 廉价档跑批量必 OOM）
9. **省钱默认**：`done --task-id` 自动暂停实例（按秒计费）

## 已知限制

- 1080p 生成在 32G 卡上 10s/15s 会 OOM——用 768p + VSR 2K 替代（效果更好且更稳）
- 15s / 768p 接近显存边界（~452s 长任务）
- 单实例同时只执行一个生成任务（平台侧排队）；大批量提速靠多实例分摊，单实例窗口并发实测仅 +6%
- VSR `quality` 非法值异步暴露（提交返回 200、轮询才见 error）
- i2v/r2v 失败任务无法自主 retry（实例重启清空上传图片），须 `submit --job` 带图重交

## 常见问题

**Q：只做一条视频也要自己装整套技能吗？**
A：是的，但只需要 `suanli-h3-video` 一个 skill（与官方 gongji-skills 的部署 skill 配合更佳，但本 skill 的 api/ 文档已覆盖全部所需接口）。

**Q：生成的视频没有声音？**
A：提示词里写 `Audio: ...`。H3 是音画一体模型，不写声音描述可能静音。

**Q：批量跑一半实例挂了怎么办？**
A：Agent 用 `submit --job jobs/<ts>/` 续跑，已完成任务自动跳过；i2v/r2v 任务需带图重交（文档有说明）。

**Q：怎么最省钱？**
A：攒够一批再开实例（冷启动 ~¥0.10 摊薄）；短试错用 480p；生成完立刻 `done --task-id` 关机。

**Q：和官方按条计费的 API 比呢？**
A：H3 官方 2K 定价约 ¥7.80/分钟（¥0.13/秒）；本方案 5 秒 2K 约 ¥0.09——约为官方的 1/7，代价是要等 ~95 秒出片（官方 API 更快）。

## License

MIT
