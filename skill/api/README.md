# 共绩 Open API 公共规范

所有请求走 `https://openapi.suanli.cn`（`SUANLI_BASE_URL` 可覆盖）。

## Header（全部请求）

```
token: <SUANLI_TOKEN>          # 简易模式密钥
timestamp: <毫秒时间戳>
version: 1.0.0
Content-Type: application/json # POST 时
```

## 响应格式

`{ "code": "0000", "message": "success", "data": {...} }`；`code !== "0000"` 即失败（`A002` = 接口无权限）。

## 计费换算

| 换算 | 公式 |
|------|------|
| 元 ↔ 点 | `1 元 = 1,000,000 点` |
| 卡时价 → 点/秒 | `卡时价(元/时) × 1,000,000 / 3600` |
| 任务消耗（元） | `billing_value / 1,000,000`（已出账）+ `forecast_value / 1,000,000`（实时）|

出账周期 5 分钟。5090 单卡参考价 ¥3.25/时 ≈ 903 点/秒。

## 资源查询（部署前必做）

```
GET /api/deployment/resource/search?page=1&page_size=50
```

筛选 `device_name` 含 5090 且 `regions[k].inventory > 0` 的条目，取 `regions[k].mark`（长加密串）与 `price`。
**必须检查 `regions[k].resource.memory ≥ 128GB`**——58GB 档跑 H3 批量会 OOMKilled。
