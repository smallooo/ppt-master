# PPT Master 微信小程序

PPT Master 服务端的官方小程序客户端，覆盖：登录 → 选模板新建项目 → 上传素材（文件 / URL）→ 等待确认 → 一键生成 → 实时进度 → 下载 PPTX。

## 目录结构

```
miniprogram/
├── app.js / app.json / app.wxss     # 应用入口与全局样式
├── sitemap.json
├── project.config.json              # 微信开发者工具配置
├── utils/
│   ├── request.js                   # 统一 wx.request 封装（错误体、token 注入）
│   ├── auth.js                      # 微信登录 + token 持久化
│   └── api.js                       # 业务接口集中导出
└── pages/
    ├── index/                       # 首页：项目列表、登录入口、新建按钮
    ├── create/                      # 新建项目（选模板 + 页数）
    ├── detail/                      # 详情：上传 / 确认 / 生成 / 产物
    └── me/                          # 个人中心 + 退出登录
```

## 与服务端的接口映射

| 页面 | 调用 | 后端路径 |
|---|---|---|
| 任意 | `auth.loginWithWeChat()` | `POST /api/v1/auth/wechat/login` |
| me | `api.me()` / `api.quota()` | `GET /api/v1/mini/me`、`/quota` |
| me | `auth.logout()` | `POST /api/v1/auth/logout` |
| index | `api.listProjects()` | `GET /api/v1/mini/projects` |
| create | `api.templates()` | `GET /api/v1/mini/templates` |
| create | `api.createProject()` | `POST /api/v1/mini/projects` |
| detail | 上传文件 / URL | `POST /sources` 与 `POST /sources/url` |
| detail | 列出 / 删除来源 | `GET /sources`、`DELETE /sources/{id}` |
| detail | 完成上传 | `POST /sources/finalize` |
| detail | 用户端确认 | `GET /confirmation` |
| detail | 生成 / 取消 / 进度 / 事件 | `/jobs/generate`、`/jobs/{id}/cancel`、`/jobs/latest`、`/jobs/{id}/events` |
| detail | 下载产物 | `/download/pptx`、`/download/{artifact_id}`、`/preview` |
| detail | 修改 / 删除项目 | `PATCH` / `DELETE /projects/{id}` |

> 八项确认的 approve / reject 由运营在管理后台用 `X-Admin-Token` 完成，小程序只展示状态。

## 启动步骤

1. **配置后端域名**
   - 编辑 [`app.js`](app.js) 顶部的 `baseUrl`，改成你的服务公网地址（必须 HTTPS，HTTPS 证书有效）。
   - 在微信公众平台 → 开发设置 → 服务器域名里把这个域名加入到「request 合法域名」「uploadFile 合法域名」「downloadFile 合法域名」三个白名单。
   - 本地联调时，可以在微信开发者工具 → 详情 → 本地设置里勾选「不校验合法域名…」。

2. **填写 AppID（可选）**
   - 编辑 [`project.config.json`](project.config.json)，把 `appid` 改成自己的小程序 AppID。空着也能在开发者工具里以「测试号」形式打开。

3. **打开项目**
   - 用微信开发者工具「导入项目」→ 选择本目录（`miniprogram/`）。
   - 编译后第一次会提示登录；点「微信一键登录」即可。

4. **CORS / 跨域**
   - 服务端 `.env` 里设置 `PPT_SERVICE_CORS_ALLOW_ORIGINS=*` 或精确写小程序对应的开发域名。

5. **典型生产链路**
   - 用户在小程序新建项目 → 上传 → finalize → 等待确认（由运营在管理后台 approve）→ 用户回到详情页点「开始生成」→ 详情页 2 秒轮询 `/jobs/latest` 与 `/jobs/{id}/events` 直到 succeeded → 下载 PPTX。

## 已知限制 / 后续可加

- `wx.openDocument` 在部分客户端对 PPTX 支持有限；可以先 `wx.saveFileToDisk` 后让用户导出。
- 没有内置 SSE，进度采用 2s 轮询，足够生产用；要更顺滑可改为 WebSocket。
- 没有写小程序原生分享卡片样式，需要 `onShareAppMessage` 时再补。
- 管理端（admin approve / reject）目前默认只在后端 + curl 用，没有做小程序后台。如需，可在 me 页加入 `X-Admin-Token` 输入并切换"运营模式"。
