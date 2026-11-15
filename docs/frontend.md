# 网页开发与验收

前端位于 `web/`，使用 React、TypeScript 与 Vite，视觉以根目录 [design.md](../design.md) 为准。页面通过 `/api` 访问后端；模型与地图密钥只由 Python 服务读取。

构建、19 组浏览器交互及容器验证结果见[网页验收记录](validation/2026-11-15-frontend.md)。

行程要求可录入抵达、离开与预约时间；活动支持增删、地点和时间替换、排序及指定跨日移动。历史草案只读，要求变化后需重新生成再编辑。营业时间与费用依据按具体活动、日期展开显示。资料可查看正文片段、原文下载、搜索定位和删除；偏好由用户主动保存或清空。

## 本地运行

使用 Node.js 24 LTS 与 npm，在项目根目录安装依赖，构建网页：

```sh
uv sync --locked
npm --prefix web ci
npm --prefix web run build
uv run uvicorn travel_planner.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

访问 `http://127.0.0.1:8000`。FastAPI 提供 `web/dist` 下的构建文件和页面路由回退，接口文档位于 `/api/docs`。

开发时在后端运行期间，另开终端执行：

```sh
npm --prefix web run dev -- --host 127.0.0.1
```

访问 Vite 输出的本机地址。开发服务器将 `/api` 原样代理到 `http://127.0.0.1:8000`。修改后端服务配置后需重启 Python 进程。

## 浏览器验收环境

使用固定供应商响应的实际 FastAPI 服务，可检查完整浏览器交互而不调用外部服务：

```sh
npm --prefix web run build
uv run python scripts/serve_web_acceptance.py --data-dir backups/acceptance/web-manual --port 8011
```

打开 `http://127.0.0.1:8011`。公开样例使用西安、2026-12-01 至 12-03、两人；对应固定模型、搜索和地图响应沿用后端测试样本，生成结果仅用于交互验收。

测试配置缺口时使用另一目录与端口：

```sh
uv run python scripts/serve_web_acceptance.py --data-dir backups/acceptance/web-no-config --port 8012 --missing-config
```

验收目录位于已忽略的 `backups/`。实际模型与高德验收仍使用 [后端在线验收](backend.md#可重复执行的独立验收) 的独立流程。

保持上述两个验收服务运行，安装 Chromium 后执行自动浏览器验收。每次使用新的输出目录：

```sh
npm --prefix web exec -- playwright install chromium
node scripts/accept_frontend.mjs --base-url http://127.0.0.1:8011 --missing-config-url http://127.0.0.1:8012 --output-dir backups/acceptance/web-browser-new
```

脚本写入测试行程、资料和偏好，应连接独立验收实例。输出包含 JSON 结果、页面截图和可见文字；检查桌面与 390px、320px 布局，并用 axe-core 检查 WCAG A／AA 规则。

类型、单元测试和构建检查：

```sh
npm --prefix web run typecheck
npm --prefix web test -- --run
npm --prefix web run build
npm --prefix web audit
```

## 构建与发布

Dockerfile 先在 Node.js 阶段执行 `npm ci` 和 `npm run build`，再将 `dist` 复制到 Python 运行镜像的 `/app/web/dist`。运行时仍是单个 Uvicorn 进程，首页、网页资源和 API 共用回环端口。

```sh
docker compose build app
docker compose up -d --no-build --wait app
```

无法访问 Docker Hub 时，可同时指定 `NODE_IMAGE` 和 `PYTHON_IMAGE`，见[部署说明](deployment.md)。网页源码、依赖目录及 `.env` 不复制到最终运行镜像。

## 界面文案

标题、按钮与标签使用短语；提示语使用必要的短句。页面只呈现用户任务所需的输入、操作、状态和业务结果。接口契约、实现说明与设计原因写入代码注释或开发文档；不将需求说明、功能介绍与重复的次级标签加入页面。

行程内容、个人资料、事实来源、核验问题和需要确认的假设按实际数据展示，未知项保留可辨认状态。静态空状态不填充虚构行程或金额。
