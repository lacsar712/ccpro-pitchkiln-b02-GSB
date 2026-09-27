# PitchKiln-01 · 灶台值守看板

Django 5 + PostgreSQL：灶台瓦片看板 + 右侧抽屉探针时间线，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX：局部刷新灶台网格与抽屉
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4710** |
| Postgres | **6110**（容器内 5432） |

数据库账号：`pitchkiln` / `pitchkiln` / 库名 `pitchkiln`

## 快速启动

```bash
cd PitchKiln/PitchKiln-01
docker compose up --build -d
```

浏览器打开：http://localhost:4710

演示账号：

- `admin` / `123456`（超级用户）
- `worker` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 6110，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6110
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4710
```

## 业务模型

1. **ResinLot（来脂批）**：`lotCode`、`originPlace`、`arrivalKg`、`receivedAt`
2. **FireHearth（灶台）**：`lane`、`tag`（唯一）、`resinGrade`、相位 `cold|charging|ramping|holding|drawing`
3. **CookRun（熬制值守）**：归属灶台与来脂批、`openedAt`、`closedAt`（可空）、`targetSoftPointC`
4. **SoftPointProbe（软化点探针）**：归属值守、`sampledAt`、`softPointC`、`samplerName`

**业务规则**：将灶台相位切到 `drawing`（出胶）时，进行中的 CookRun 必须至少有一条 SoftPointProbe 的 `softPointC ≤ 95`。逻辑在 `apps/kiln/services/floor_rules.py`，由相位切换入口调用。

**来脂批合并（改挂）**：主管把源来脂批并入目标来脂批。逻辑在 `apps/kiln/services/lot_merge.py`，入口 `POST /resin-lots/merge/`（`GET` 为合并表单页）。

1. **权限**：仅主管（`is_staff`）可发起；值守工发起合并一律拒绝（403）。
2. **出胶 veto**：任一批在「出胶」相位灶上挂着未收灶值守 → 整次合并拒绝，两批都不动。
3. **顺序**：先把源批的未收灶值守改挂到目标批（历史已收灶值守一并改挂，否则 PROTECT 外键不允许删批），目标批 `arrivalKg` 累加源批到货量，最后删除源批。
4. **到货地不同**：两批到货地不同时，以目标批（保留批）的到货地为准，源批到货地不再保留。合并表单与来脂批页的提示文案与此一致，后端亦按此执行。
5. **合并后**：按批筛值守（批次详情页）只见目标批；源批详情页 404 打不开；看板瓦片与抽屉批号显示目标批；来脂批卡片流上源批消失、目标批千克累加迁入量，卡片流总千克守恒（0 差）。

## 界面

- 首页：**灶台值守看板** — 左侧班次条 + 按过道排布的灶台瓦片；点瓦片打开右侧抽屉（值守、探针时间线、改相位 / 登记探针 / 开灶）
- 次页：**来脂批** — 卡片时间线，非宽表 CRUD；点批号进批次详情（按批筛值守）；主管另见合并表单

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有灶台则只保证账号存在。样例地名仅用「松脂坳 / 桐油坑」系。

## 目录结构

```
PitchKiln-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/
  apps/kiln/          # 模型、视图、floor_rules、种子
  templates/floor/    # 值守看板 + 抽屉
  templates/resin/    # 来脂批时间线
  static/css/         # 值守台 ops-console 样式
```
