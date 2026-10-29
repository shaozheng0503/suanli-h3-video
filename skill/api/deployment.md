# 实例生命周期（H3 镜像专用）

镜像：`harbor.suanleme.cn/public-hub/h3vsr-merge:0.1.4-5090`（ComfyUI 生成 8188 + RTX VSR 超分 8001 一体）

## 创建任务

```
POST /api/deployment/task/create
{
  "task_name": "h3-video-<日期>",
  "points": 1,
  "resources": [{
    "resource": {
      "device_name": "5090", "region": "<查库存所得>", "gpu_name": "5090",
      "gpu_count": 1, "gpu_memory": 32600, "memory": <实际值>, "cpu_cores": <实际值>
    },
    "region_name": null,
    "mark": "<查库存所得>",
    "weight": null
  }],
  "services": [{
    "service_name": "container-01",
    "service_image": "harbor.suanleme.cn/public-hub/h3vsr-merge:0.1.4-5090",
    "remote_ports": [{"service_port": 8188}, {"service_port": 8001}],
    "env": "NVIDIA_DRIVER_CAPABILITIES=compute,utility,video"
  }]
}
```

返回 `{"data": {"task_id": 3571477}}`；域名在 task/search 结果里：
`https://deployment-452-<随机串>-8188.550w.link` 与 `...-8001.550w.link`。

## 探活

- **8188（ComfyUI）**：`GET {域名}/system_stats` → HTTP 200 即活。无 `/health` 端点。冷启动 1~3 分钟可能拒绝连接，需重试（间隔 30s，最多 10 次）
- **8001（VSR）**：`GET {域名}/healthz` → JSON 含 `status`、`concurrency`、`queue_visible`

## 查状态 / 暂停 / 恢复

```
GET  /api/deployment/task/search?page=1&page_size=5     # 列表（含 remote_ports 域名、billing/forecast）
POST /api/deployment/task/pause   {"task_id": <id>}      # 暂停（释放资源，停止计费）
POST /api/deployment/task/recover {"task_id": <id>}      # 恢复（重新调度，域名不变）
```

**注意**：pause 后再 recover 会重新调度节点，ComfyUI 冷启动 + 队列/history 清空，断点续跑靠本地 job.json。

## 约束

- 单实例同时只处理一个生成任务（多任务自动排队）
- 请求体上限 100MB（R2V 大图注意）
- `points` = 节点数（1 卡 = 1），与计费"点"是两个概念
