# PPT Master — 服务端

供小程序/前端调用的 HTTP 后端。把仓库里的 PPT 生成管线封装为多租户服务。

## 当前能力（截至 P0）

- `.env` 驱动的配置（pydantic-settings）
- 独立项目工作区（`.service-data/projects/<uuid>/`）
- 源文件上传、归一化为 markdown
- 人工确认（design_spec / spec_lock）
- 单进程 worker，串行执行生成任务
- 兜底 PPTX 导出（占位，便于打通 e2e；P3 替换为真实 SVG→PPTX 管线）
- 容器化部署（Dockerfile + docker-compose + Nginx 示例）

阶段路线图见仓库根 `README.md` 的“服务端阶段”小节。

## 本地运行

### 方式一：直接启动

```bash
cp .env.example .env        # 填入 OpenAI / 微信 / DB 真实值
pip install -r requirements.txt
python -m service           # 等价于 uvicorn service.api.app:app
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

### 方式二：docker-compose（含 PostgreSQL + Nginx）

```bash
cp .env.example .env
docker compose up --build
# API:        http://127.0.0.1:8000
# Nginx 入口: http://127.0.0.1:8080
```

## 目录结构

```
service/
  __main__.py           # python -m service 入口
  config.py             # ServiceSettings(.env)
  api/
    app.py              # FastAPI factory
    runtime.py          # workspace_manager / job_runner 单例
    routes/
      projects.py       # /api/v1/mini/projects/*
      admin.py          # /api/v1/admin/*
  core/workspace.py     # WorkspaceManager（manifest 持久化，P1 切换 PG）
  workers/job_runner.py # 进程内串行 worker
  adapters/             # source_normalizer / fallback_pptx_exporter
  storage/              # LocalStorageBackend
  schemas/              # Pydantic 请求/响应
  models/enums.py       # 状态机
```

## 端点速览

小程序面向：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/mini/projects` | 创建项目 |
| GET  | `/api/v1/mini/projects/{id}` | 项目状态 |
| POST | `/api/v1/mini/projects/{id}/sources` | 上传源文件（multipart） |
| POST | `/api/v1/mini/projects/{id}/sources/finalize` | 完成上传，进入归一化 |
| POST | `/api/v1/mini/projects/{id}/jobs/generate` | 排入生成任务 |
| GET  | `/api/v1/mini/projects/{id}/jobs/latest` | 查询最新任务 |
| GET  | `/api/v1/mini/projects/{id}/artifacts` | 列出产物 |
| GET  | `/api/v1/mini/projects/{id}/download/pptx` | 下载主 PPTX |

管理/内部：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/v1/admin/projects/{id}/confirmation` | 取确认包 |
| POST | `/api/v1/admin/projects/{id}/confirmation/approve` | 批准 |

## 注意

- 当前持久化为 manifest 文件，P1 阶段切到 PostgreSQL。
- 当前导出为 fallback PPTX，P3 阶段替换为真实 SVG→PPTX 管线。
- 鉴权未启用，P2 接入微信 openid + session token 后会强制 `/api/v1/mini/*` 鉴权。
