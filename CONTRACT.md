# 团队开发契约（中国网球公开赛赛事运营管理系统）

> 本文件是各模块开发的唯一约定。开始编码前**必须完整阅读**，编码时**不得修改**本文件及他人负责的文件。
> 项目根目录：`/Users/ryanhu/Desktop/大学课程/大三上/管理信息系统/管理信息系统分析与设计/china_open_mis`

---

## 1. 技术栈与目录结构

- 后端：Python 3.13 + Flask 3.1（应用工厂 + 蓝图）
- 数据库：MySQL 8.0（PyMySQL，`DictCursor`，字段名小写下划线）
- 前端：Bootstrap 5.3（已离线内置）+ Bootstrap Icons + 少量自定义 CSS
- 表名 `match` 是 MySQL 保留字，SQL 中必须写成 `` `match` ``

```
china_open_mis/
├── app.py                  # 启动入口（Lead 负责）
├── config.py               # 数据库/会话/赛事周期配置（Lead）
├── requirements.txt
├── .env.example            # 复制为 .env 并填写 MySQL 密码
├── sql/01_schema.sql       # 建表脚本（Lead，禁止改）
├── sql/02_seed.sql         # 演示数据（seed 负责人）
├── tools/init_db.py        # 一键建库建表导入数据（Lead，禁止改）
├── app/
│   ├── __init__.py         # 应用工厂/会话守卫/模板全局函数（Lead，禁止改）
│   ├── db.py               # 数据库访问封装（Lead，禁止改）
│   ├── services.py         # 公共查询与字典（Lead，禁止改）
│   ├── auth.py             # 登录/角色/菜单/操作日志（Lead，禁止改）
│   ├── validation.py       # 冲突校验与比分校验（Lead，禁止改）
│   ├── modules/
│   │   ├── dashboard.py    # 工作台（Lead，禁止改）
│   │   ├── system.py       # 模块1 系统管理
│   │   ├── base_data.py    # 模块2 基础数据
│   │   ├── schedule.py     # 模块3 赛程编排
│   │   ├── results.py      # 模块4 成绩管理
│   │   └── public.py       # 模块5 公开查询
│   ├── templates/
│   │   ├── base.html       # 基础布局（Lead，禁止改）
│   │   ├── login.html / dashboard.html / 403.html ...（Lead）
│   │   ├── system/  base_data/  schedule/  results/  public/   # 各模块自己负责
│   └── static/{css,js,vendor}   # 共享，禁止改
└── tests/                  # 端到端测试脚本
```

**写文件范围**：只允许创建/修改自己任务里列出的文件。发现别人文件有问题，发消息给 Lead，不要自己改。

---

## 2. 运行与自测环境（所有同学统一用这套）

MySQL 测试实例已经起在 **127.0.0.1:3307**（root，空密码），数据库名 `china_open`，已导入演示数据。
PyMySQL 装在 `/tmp/pydeps`（系统 Python 装不了包，必须用 PYTHONPATH）。

```bash
cd "/Users/ryanhu/Desktop/大学课程/大三上/管理信息系统/管理信息系统分析与设计/china_open_mis"

# 启动服务（每人用自己分配到的端口，避免互相冲突）
PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= APP_PORT=<你的端口> APP_DEBUG=0 python3 app.py &

# 用 curl 自测（携带 cookie 登录）
curl -s -c /tmp/c_<你>.txt -b /tmp/c_<你>.txt -d "login_name=admin01&password=123456" \
     -X POST http://127.0.0.1:<你的端口>/login -o /dev/null -w "%{http_code}\n"
curl -s -b /tmp/c_<你>.txt http://127.0.0.1:<你的端口>/<你的页面> | head -50
```

也可以完全不启服务，用 Flask 测试客户端（推荐写小脚本自测）：

```python
import os
from app import create_app
app = create_app('config')          # 环境变量里带上 DB_PORT=3307 DB_PASSWORD=
c = app.test_client()
c.post('/login', data={'login_name': 'admin01', 'password': '123456'})
r = c.get('/base/player')
print(r.status_code, r.data.decode()[:2000])
```

- **禁止**执行 `tools/init_db.py` 对共享 `china_open` 库重新初始化（会清空数据）；需要重置时找 Lead。
- 需要验证 SQL 时可以直接用命令行：`mysql -h 127.0.0.1 -P 3307 -u root --protocol=TCP china_open -e "select * from player"`。

**演示账号**：`admin01` / `schedule01` / `record01`，密码都是 `123456`。

---

## 3. 数据层 API（`from .. import db`）

```python
db.query_all(sql, params=None)   -> list[dict]      # 查询多行
db.query_one(sql, params=None)   -> dict | None     # 查询一行
db.query_value(sql, params=None, default=None)      # 查询单个值
db.execute(sql, params=None)     -> int             # INSERT/UPDATE/DELETE，自动提交
db.execute_many(sql, seq)        -> int             # 批量写
with db.transaction() as cur:    # 多步写操作原子事务（失败整体回滚）
    cur.execute('UPDATE ...', (a,))
    cur.execute('UPDATE ...', (b,))
    # 事务内取查询结果：cur.fetchone() / cur.fetchall() 返回 dict
```

注意事项：
- 所有 SQL 参数必须用 `%s` 占位并作为参数传入，**禁止字符串拼接用户输入**；
- `match_date` 查询出来是 `datetime.date`，展示时用 `.isoformat()` 或 `strftime`；
- `datetime` 字段（`created_at` 等）是 `datetime.datetime`；
- 布尔类字段没有，全部用 `CHAR(1)` 代码值。

---

## 4. 公共查询与字典（`from .. import services`）

```python
# 字典（数据库存代码，界面显示中文）
services.EVENT_NAMES    # {'MS':'男子单打','WS':'女子单打','MD':'男子双打','WD':'女子双打','XD':'混合双打'}
services.ROUND_NAMES    # {'Q':'资格赛','R1':'首轮','R2':'第二轮','QF':'八强','SF':'四强','F':'决赛'}
services.MATCH_STATUS   # {'N':'未开始','P':'进行中','F':'已结束'}
services.COURT_STATUS   # {'A':'可用','M':'维护中'}
services.DUTY_STATUS    # {'A':'可排','B':'已排','C':'停用'}
services.STAFF_ROLES    # {'admin':'系统管理员','scheduler':'赛事编排员','recorder':'成绩录入员'}
services.event_name(code) / round_name(code) / status_name(code)
services.court_status_name(code) / duty_status_name(code) / role_name(code)

# 比赛查询（返回已拼好展示字段的 dict 列表）
services.list_matches(date=None, court_id=None, event_code=None, status=None,
                      statuses=None, player_id=None, keyword=None, limit=None)
# 每条比赛已带：event_name, round_name, status_name, court_status_name, start_end,
#               team1/team2（选手 dict 列表）, team1_text/team2_text, player_ids
services.get_match(match_id)                 # 单场（找不到返回 None）
services.fetch_players(match_ids)            # {match_id: [选手行]}
services.list_players(nation=, gender=, rank_min=, rank_max=, keyword=, wildcard=)
services.list_courts(available_only=False, keyword=None)
services.list_referees(status=None, keyword=None, available_only=False)
services.list_nations()
services.dashboard_stats(day=None)           # 工作台统计
services.today_str() / services.default_show_date()
services.in_event_period(day)                # 是否在赛事周期内
services.end_time('14:00', 120)              # -> '16:00'
services.next_serial('player','player_id','P',7)   # -> 'P0000017'
```

## 5. 业务规则（`from .. import validation`）

```python
# 赛程编排：表单校验 + 三类冲突校验（模块3 直接用）
data, errors = validation.validate_schedule_form(request.form)
# data: {event_code, round_code, court_id, referee_id, match_date, start_time,
#        duration_min, players: [(player_id, team_no), ...]}
# errors: {字段名: 中文错误说明}，空 dict 表示格式通过
conflicts = validation.check_conflicts(data['match_date'], data['start_time'],
                                       data['duration_min'], data['court_id'],
                                       data['referee_id'], data['players'],
                                       exclude_match_id=None)
# 返回 [] 表示无冲突；否则每项 {kind:'player'|'court'|'referee', title, message, match_id, suggestion}
validation.suggest_available_courts(date, start_time, duration_min)   # 冲突时给出候选场地
validation.suggest_available_referees(date, start_time, duration_min)
validation.scan_conflicts()                  # 全量扫描现有冲突（工作台用）

# 成绩录入
data, errors = validation.validate_score(request.form)   # {set_score, game_score, actual_min}
ok, msg = validation.can_enter_score(match_row)          # BR-04：只有"进行中"才能录分
```

**表单字段约定（模块3 必须遵守）**：对阵选手以逗号分隔字符串提交：
`team1_players`（如 `P0000001,P0000002`）、`team2_players`；单打每队 1 人、双打每队 2 人。

---

## 6. 模板约定

### 6.1 布局
所有内部页面：`{% extends 'base.html' %}`，在 `{% block content %}` 里写正文。可用块：

```jinja
{% block title %}页面标题 · {{ app_short }}{% endblock %}
{% block page_title %}顶部标题{% endblock %}
{% block page_sub %}顶部副标题（可选，说明性文字）{% endblock %}
{% block head_extra %}{% endblock %}   {# 额外 CSS/JS #}
{% block content %} ... {% endblock %}
{% block scripts %} ... {% endblock %}
```

观众端页面同样 extends `base.html`（未登录时自动切换为公开版式，`current_staff` 为空）。

**注意**：`page_title/page_sub` 只在内部人员版式中生效，观众端可省略。
**链接一律用 `url_for`**；如果不是本模块的端点，用 `{{ safe_url('other.endpoint') }}` 兜底。
删除按钮提示：`<form method="post" data-confirm="确定删除吗？" ...>`（app.js 会统一处理确认框）。

### 6.2 全局可用的模板变量（来自 context processor）
`current_staff`（dict：staff_id/name/role/role_name）、`menus`、`role_name()`、
`EVENT_NAMES`、`ROUND_NAMES`、`MATCH_STATUS`、`COURT_STATUS`、`DUTY_STATUS`、`STAFF_ROLES`、`STAFF_STATUS`、
`MATCH_BADGE`（状态→bootstrap 颜色）、`COURT_BADGE`、`DUTY_BADGE`、
`event_name()`、`round_name()`、`status_name()`、`court_status_name()`、`duty_status_name()`、
`end_time()`、`today()`、`event_start`、`event_end`、`app_name`、`app_short`、`safe_url()`。

示例：

```jinja
<span class="badge text-bg-{{ MATCH_BADGE[m.status] }}">{{ m.status_name }}</span>
{{ url_for('schedule.create') }}
{{ safe_url('system.log_list') }}
```

### 6.3 提示消息（flash）
```python
flash('保存成功。', 'success')     # success / danger / warning / info
```
布局里已统一渲染，不需要自己写 alert 容器。

### 6.4 样式约定（Bootstrap 5 + 自定义类）
- 卡片：`<div class="card"><div class="card-header">…</div><div class="card-body">…</div></div>`
- 表格：`<table class="table table-hover align-middle mb-0">`，外层套 `<div class="table-responsive">`
- 表单：`<label class="form-label required">`、`<input class="form-control">`、`<select class="form-select">`
- 空状态：`<div class="empty-hint">暂无数据</div>`
- 统计卡片：`<div class="card stat-card stat-primary"><div class="card-body">…</div></div>`
- 冲突提示：`<ul class="conflict-list"><li><span class="conflict-title">…</span><div class="conflict-suggest">…</div></li></ul>`
- 快捷入口：`<a class="quick-link">…</a>`
- 页面标题栏节奏：卡片间距用 `mb-3`，栅格用 `row g-3`

### 6.5 权限
- 蓝图级权限已在 `app/__init__.py` 的 `before_request` 统一校验（非本角色访问返回 403 页）；
- **写操作视图**（新增/修改/删除/状态变更）必须再加 `@auth.require_roles('admin', ...)`；
- 模块1 系统管理只有 `admin`；模块2/3 是 `admin, scheduler`；模块4 是 `admin, recorder`；模块5 不登录也能访问。

### 6.6 操作日志（2.4 节要求：删除与状态变更必须留痕）
每次删除、状态变更、成绩录入后调用：
```python
from .. import auth
auth.log_operation('删除', 'player:P0000001', '删除运动员 张之维')
auth.log_operation('状态变更', 'court:C00001', '场地状态 可用 -> 维护中')
auth.log_operation('录入成绩', 'match:M260005', '盘分 2:1 局分 6-4 3-6 7-5')
```
`log_operation` 会自己读取当前登录人，失败不影响主流程。

---

## 7. 各模块必须实现的端点（端点名=视图函数名，菜单/工作台已引用，不可改名）

| 模块 | 端点 | 路径 | 说明 |
| --- | --- | --- | --- |
| 系统管理 | `system.staff_list` | GET `/system/staff` | 账号列表 + 新增/改角色/重置密码/启停用（admin） |
| | `system.staff_create` | POST `/system/staff/create` | 新增账号 |
| | `system.staff_update` | POST `/system/staff/<staff_id>/update` | 改姓名/岗位/角色 |
| | `system.staff_reset` | POST `/system/staff/<staff_id>/reset` | 重置密码为 123456 |
| | `system.staff_toggle` | POST `/system/staff/<staff_id>/toggle` | 启用/停用 |
| | `system.log_list` | GET `/system/logs` | 操作日志列表（可按操作类型筛选） |
| 基础数据 | `base_data.player_list` | GET `/base/player` | 运动员列表 + 按国籍/性别/排名区间/关键字检索 + 新增/编辑/删除 |
| | `base_data.player_save` | POST `/base/player/save` | 新增或修改（隐藏字段 player_id 为空=新增） |
| | `base_data.player_delete` | POST `/base/player/<player_id>/delete` | 删除前校验未结束比赛（FR-03） |
| | `base_data.referee_list` | GET `/base/referee` | 裁判列表 + 新增/编辑/删除 + 排班状态下拉切换 |
| | `base_data.referee_save` | POST `/base/referee/save` | |
| | `base_data.referee_status` | POST `/base/referee/<referee_id>/status` | 可排/已排/停用 |
| | `base_data.referee_delete` | POST `/base/referee/<referee_id>/delete` | |
| | `base_data.court_list` | GET `/base/court` | 场地列表 + 新增/编辑/删除 + 状态下拉切换（表 11：左场地右裁判也可以分页做） |
| | `base_data.court_save` | POST `/base/court/save` | |
| | `base_data.court_status` | POST `/base/court/<court_id>/status` | 可用/维护中，改维护时提示影响场次 |
| | `base_data.court_delete` | POST `/base/court/<court_id>/delete` | |
| 赛程编排 | `schedule.index` | GET `/schedule/` | 上半区编排表单 + 下半区当日赛程；冲突红字显示、表单内容保留 |
| | `schedule.create` | POST `/schedule/create` | 校验 + 保存比赛（写 `match` 与 `match_player`，事务） |
| | `schedule.list_all` | GET `/schedule/list` | 赛程表：按日期/场地/项目筛选 + 导出 CSV |
| | `schedule.delete` | POST `/schedule/<match_id>/delete` | 删除比赛（级联删参赛记录，留日志） |
| | `schedule.change_status` | POST `/schedule/<match_id>/status` | 状态切换（未开始/进行中） |
| | `schedule.export_csv` | GET `/schedule/export` | 导出当前筛选结果为 CSV（UTF-8 BOM） |
| 成绩管理 | `results.index` | GET `/results/` | 左侧未结束比赛列表 + 右侧录分表单（盘分/局分明细/实际时长） |
| | `results.start_match` | POST `/results/<match_id>/start` | 未开始 → 进行中 |
| | `results.save_score` | POST `/results/<match_id>/score` | 事务：写比分+置 F+累加双方胜负场次 |
| | `results.stats` | GET `/results/stats` | 统计报表：按项目/场地/性别胜负分布 + 平均时长 + 导出 |
| | `results.export_stats` | GET `/results/stats/export` | 导出统计 CSV |
| 公开查询 | `public.index` | GET `/public/` | 日期选择 + 当日赛程（含比分），无任何编辑入口 |
| | `public.players` | GET `/public/players` | 选手资料查询（按姓名/国籍/项目） |
| | `public.player_detail` | GET `/public/player/<player_id>` | 选手详情 + 参赛记录 + 胜率 |

模板文件（每个模块只建自己的）：
`templates/system/staff.html`、`templates/system/logs.html`；
`templates/base_data/player.html`、`referee.html`、`court.html`；
`templates/schedule/index.html`、`list.html`；
`templates/results/index.html`、`stats.html`；
`templates/public/index.html`、`players.html`、`player_detail.html`。

---

## 8. 交付要求

1. **能跑通**：页面 200、CRUD 真的能写库、错误输入有中文提示、不出现 500；
2. 表单校验在前端（HTML5 必填/格式）与后端（Python）都要有；
3. 关键业务规则必须走 `validation.py`，不要自己另写一套；
4. 所有写操作后 `flash` 中文结果提示，并在需要时写 `auth.log_operation`；
5. 代码注释用中文，函数写 docstring，命名清晰（课程设计要交源码）；
6. 自测通过后：把测试命令与结果写进任务完成说明（给 Lead 复核）。

---

## 9. 与报告对应关系（写文档/答辩时可引用）

- 2.2 角色权限 → `auth.py` 菜单与 `BLUEPRINT_ROLES`
- 2.3 功能需求 FR-01～FR-12 → 各模块页面功能（见上表）
- 2.5 业务规则 BR-01～BR-04 → `validation.py`
- 4.3 赛程编排处理流程 → `schedule.py` + `validation.check_conflicts`
- 4.5 数据库设计 → `sql/01_schema.sql`（6 张表 + 日志表 + 视图）
- 4.6 界面清单 → 各模块模板
- 4.7 权限与异常处理 → `app/__init__.py` 的 `guard` 与 errorhandler
