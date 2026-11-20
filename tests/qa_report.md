# T6 独立验收审查报告（中网赛事运营管理系统）

> 审查人：独立验收审查员（QA，黑盒）　审查对象：`china_open_mis`（Python 3 + Flask + MySQL 实现）
> 审查时间：2026-11-20 09:38—09:52（Asia/Shanghai）
> 审查原则：**不修改任何业务代码**、不轻信既有汇报；所有结论均以实际 HTTP 请求（Flask test_client 与 127.0.0.1:8780 的真实服务 curl）＋直接 SQL 查库核对为依据。
> 本报告是本次审查唯一写入的项目文件；业务代码 `git` 工作区未被 QA 改动。

## 审查环境与基线

| 项目 | 值 |
| --- | --- |
| 应用版本快照 | app/*.py、modules/*.py 修改时间 09:25—09:33；审查期间 README 于 09:46 被他人更新、docs/ 于 09:39—09:40 新增两份文档，均已纳入核对 |
| Python / Flask / Werkzeug / PyMySQL | 3.13.5 / 3.1.0 / 3.1.3 / 1.2.4（`PYTHONPATH=/tmp/pydeps`） |
| MySQL | 8.4.0 @127.0.0.1:3307，root 空密码，库 `china_open` |
| 演示数据（审查前快照） | player 40 / referee 12 / court 10 / match 36 / match_player 108 / staff 3；状态分布 **F 17 / P 3 / N 16** |
| 既有基线脚本 | `tests/test_e2e.py` 复跑结果 **49/49 通过**（证据：/tmp/qa_e2e_baseline.txt） |
| 自建独立检查 | 251 项（`/tmp/qa_harness.py`，S0—S11）＋ 2 组补充探针（`/tmp/qa_probe.py` 等），结果 **230/251 PASS、21 FAIL**（去重后见下方缺陷清单） |
| 真实服务验证 | `PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= APP_PORT=8780 APP_DEBUG=0 python3 app.py` 启动成功，curl 验证观众端/登录/静态资源/403/导出均正常 |

> 说明：`tests/test_e2e.py` 会真实修改演示库（实测把一场 P 比赛录成 F，状态由 F17/P3 变为 F18/P2，且脚本不还原）。本次审查前已用 `mysqldump` 备份，所有写操作测试结束后已还原（见文末“测试数据与还原”）。

---

## 一、结论概要

### 1. 功能需求覆盖率

**FR-01～FR-12：12/12 通过（100%）。** 每条需求都用实际请求独立验证，未发现功能缺失或流程不通；49 项既有端到端用例复跑 49/49 通过，与实现一致。

### 2. 缺陷统计

| 等级 | 数量 | 说明 |
| --- | --- | --- |
| **P0（阻断演示）** | **0** | 主流程（登录、基础数据、编排、录分、统计、观众端、导出）全部可演示 |
| **P1（功能/权限不符验收要求）** | **2** | ① 未登录可通过登录 `next` 参数触发 HTTP 500；② 角色被降级/账号被停用后旧会话仍有效，可继续越权写库 |
| **P2（体验/安全加固/数据一致性）** | **12** | 开放重定向、修改密码提示丢失、非法状态静默切换、删除已结束比赛不改胜负场次、`back` 参数 CRLF 触发 500、CSV 公式注入、用户枚举、CSRF/SameSite、默认 debug 模式、数据库错误原文回显、已结束比赛录分页无锁定提示、演示数据漂移 |
| 文档不一致 | 7 组 | 见第四节 |

### 3. 其他验收项结论

| 审查项 | 结论 |
| --- | --- |
| 权限矩阵（admin/scheduler/recorder/未登录 × 10 个内部路由 + 观众端）（清单 3） | **PASS**：与报告 2.2 节一致；未登录全部 302 登录；越权 POST 全部 403 且库无变化；TC-08 403 页面不含编排数据 |
| 边界与异常输入（空值/超长/负数/非法日期/非法枚举/SQL 关键字/引号/emoji/XSS/路径穿越/超大请求）（清单 2） | 46 项边界用例未出现 500/乱码；仅发现 CRLF 头注入类两处 500（QAD-01、QAD-07） |
| 数据完整性（外键/唯一键/CHECK/级联/重复排场/删除被引用数据）（清单 4） | **PASS**：4 个外键、`uk_match_court_slot` 唯一键、11 个 CHECK 均在库上生效；删除被引用运动员/裁判/场地均有中文提示不 500；删除比赛级联清理参赛记录 |
| 状态机 N→P→F（F 不可改、N 不能直接 F、F 不能再录分）（清单 5） | **PASS（含 1 处 P2 偏差）**：F 后状态/录分/重新开始均被拒；成绩页对 N 录分被拒；N→F 无合法入口；但赛程页状态接口对非法目标 `status=F` 静默把 P 切回 N（QAD-05） |
| 导出（赛程/统计 CSV 的 Content-Type、BOM、中文表头、行数一致）（清单 6） | **PASS**：UTF-8 BOM、16 列中文表头、行数与筛选/列表页一致、`filename*=UTF-8''` 中文文件名均正确 |
| 观众端只读（三页面无 form/post/编辑入口）（清单 7） | **PASS**：3 个公开页面无 `<form>`/`method=post`/编辑删除入口，匿名 POST 不写库 |
| 静态资源离线化（无外网 CDN）（清单 8） | **PASS**：模板无任何 http(s) 外链；Bootstrap/图标/字体/CSS/JS 全部来自 `/static` 且均 200；CSS 中无 `url(http…)`/`@import` |
| 文档一致性（清单 9） | **存在 7 组不一致**，详见第四节（README 于审查期间被更新，已按最新版核对） |

---

## 二、FR-01～FR-12 逐条结论

| 需求 | 验证方式（实际请求） | 结果 |
| --- | --- | --- |
| FR-01 登录后按角色显示菜单 | 三个演示账号 POST `/login`；对比工作台 HTML 菜单；错误密码/空值/SQL 注入登录；未登录访问内部页 | **PASS**。菜单与角色一致；错误输入有中文提示；未登录 302 到登录页 |
| FR-02 管理员新增账号/指定角色/重置密码并留痕 | 建临时账号→停用→启用→重置密码→改资料；查 `staff` 与 `sys_log`；测最后一名管理员保护 | **PASS**。哈希非明文；日志含操作人/时间/操作类型；临时账号已清理 |
| FR-03 运动员增删改；删除前校验未结束比赛 | 页面新增/修改/删除；删除有 N 比赛的运动员；删除仅有 F 比赛的运动员；删除不存在编号 | **PASS**（实现比 FR 更严格：只被已结束比赛引用也拒绝删除，避免破坏历史成绩，可接受；建议在文档注明） |
| FR-04 国籍/性别/排名区间组合检索且 2 秒内 | 6 组查询（单条件/组合/非法参数/300 字关键字）；计时 | **PASS**。组合检索 0.02s 级返回；非法参数被忽略；无 500 |
| FR-05 裁判档案与排班状态 A/B/C | 新增/修改；A→B→C→A 切换；非法状态；删除被引用裁判 | **PASS**。状态落库正确、写日志；非法值中文提示 |
| FR-06 场地档案/状态；维护中场地不出现候选 | 新增/修改；A→M 后查 `available_only` 与编排页 HTML；M→A；非法状态；删除被引用场地 | **PASS**。维护中场地不在编排候选中，强行提交提示“场地维护中”并有候选场地建议 |
| FR-07 选择项目/轮次/选手/场地/裁判/日期/时间生成比赛 | POST `/schedule/create` 正常保存；核对 `match`+`match_player`、编号格式、日志；13 组非法表单 | **PASS**。事务写入正确；非法表单全部中文提示且不落库（NUL 等极端输入会回显数据库原文，见 QAD-12） |
| FR-08 保存前校验选手/场地/裁判三类冲突并指出对象 | 构造场地重叠、选手重叠、裁判重叠、维护场地、停用裁判、首尾相接边界 | **PASS**。均正确拒绝并给出冲突比赛编号；TC-04 边界允许保存 |
| FR-09 日期/场地/项目筛选赛程并导出 CSV | 4 种筛选组合；CSV 与列表页/数据库行数比对；BOM/表头/文件名 | **PASS**。CSV 16 列中文表头、带 BOM、行数一致（详见 S5 检查 13/13） |
| FR-10 录分、置已结束、不可重复录入 | N 比赛录分被拒→开始比赛→11 组非法比分→正常录分→重复录分→F 再开始 | **PASS**。状态/比分/实际时长落库正确；重复录入提示“成绩已锁定” |
| FR-11 自动更新胜负场次并生成统计报表 | 一队胜/二队胜各一场，比对双方 `win_count/loss_count`；统计页与 CSV 数字、场地行数 | **PASS**。胜方全体 +1 胜、负方全体 +1 负；统计页与库一致；CSV 五个分节完整 |
| FR-12 免登录查询赛程/选手/比分且无修改入口 | 匿名访问三页面、日期参数、选手检索/详情/404、只读检查、匿名 POST | **PASS**。比分录完后观众端同步可见；页面无任何编辑入口 |

> 说明：S1 中“修改密码成功提示”一项 FAIL 属提示文案丢失（QAD-04），不影响 FR-01/FR-02 功能结论；S9“非法目标状态”一项 FAIL 属状态机健壮性偏差（QAD-05）。

---

## 三、缺陷清单

> 复现命令默认已启动服务：`PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= APP_PORT=8780 APP_DEBUG=0 python3 app.py`（端口可按需改）。涉及写库的复现命令请配合文末还原脚本执行。

### P1

#### QAD-01　登录 `next` 参数含 CRLF → HTTP 500（未认证用户可触发）
- 路由：`POST /login?next=...`（`next` 也可来自表单隐藏域）
- 复现：
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -X POST \
    --data 'login_name=admin01&password=123456' \
    'http://127.0.0.1:8780/login?next=%2Ffoo%0d%0aX-Test%3A%20y'
  # 期望 302 / 中文提示；实际 500
  ```
  等价 Python：`app.test_client().post('/login?next=%2Ffoo%0d%0aX-Test%3A%20y', data={'login_name':'admin01','password':'123456'})` → 500。
- 实际：`app/auth.py:211` 直接 `redirect(referer_next)`，`referer_next` 含换行时 Werkzeug 抛 `ValueError: Header values must not contain newline characters.`；500 页显示“系统异常”。默认调试模式（QAD-11）下浏览器会直接展示 Werkzeug 调试器堆栈。
- 期望：校验 `next` 只允许本站相对路径（拒绝 `//`、`http(s)://`、`javascript:`、控制字符），非法时回落到工作台；不允许出现 500。
- 建议修复文件：`app/auth.py`（`login()`，约 163、211 行）。

#### QAD-02　角色变更/账号停用后旧会话不失效，被降权账号可继续越权写库
- 路由：所有内部写接口（示例 `POST /base/player/save`）；根源在 `app/__init__.py` 的 `guard()` 与 `app/auth.py: current_staff()` 只读会话、不查库。
- 复现（服务已启动）：
  ```bash
  # 1) 管理员建一个编排员账号
  curl -s -c /tmp/qa_admin.txt -d 'login_name=admin01&password=123456' http://127.0.0.1:8780/login -o /dev/null
  curl -s -b /tmp/qa_admin.txt -d 'name=QA会话&post=QA&login_name=qa_sess_1&password=123456&role=scheduler' \
       http://127.0.0.1:8780/system/staff/create -o /dev/null
  # 2) 该账号登录，可访问基础数据（200）
  curl -s -c /tmp/qa_sess.txt -d 'login_name=qa_sess_1&password=123456' http://127.0.0.1:8780/login -o /dev/null
  curl -s -b /tmp/qa_sess.txt -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8780/base/player        # 200
  # 3) 管理员把它降为成绩录入员
  SID=$(mysql -h 127.0.0.1 -P 3307 -u root --protocol=TCP -N china_open -e "select staff_id from staff where login_name='qa_sess_1'")
  curl -s -b /tmp/qa_admin.txt -d 'name=QA会话&post=QA&role=recorder' \
       http://127.0.0.1:8780/system/staff/$SID/update -o /dev/null
  # 4) 旧会话仍能访问并写库（实测 200 且写入成功；期望 403）
  curl -s -b /tmp/qa_sess.txt -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8780/base/player
  curl -s -b /tmp/qa_sess.txt -d 'player_id=&name_cn=QA越权&name_en=&nation=CHN&gender=M&rank_single=900&rank_double=&association=&wildcard=N' \
       http://127.0.0.1:8780/base/player/save -o /dev/null
  mysql -h 127.0.0.1 -P 3307 -u root --protocol=TCP -N china_open -e "select count(*) from player where name_cn='QA越权'"   # 1
  # 同理：管理员把该账号停用（POST /system/staff/<id>/toggle）后，旧会话访问 /dashboard/ 仍 200、仍可写 /base/referee/save
  # 清理：mysql ... -e "delete from player where name_cn='QA越权'; delete from staff where login_name='qa_sess_1';"
  ```
- 实际：降权后旧会话 `/base/player` 仍 200 并可成功写库；停用后旧会话仍可访问、仍可写。权限判断完全信任登录时写入 session 的 `role`，最长 30 分钟（会话超时）才失效。
- 期望：每次请求从库中读取当前角色/账号状态（或在角色/状态变更时使会话失效），降权/停用后立即 403 或强制重新登录。
- 建议修复文件：`app/__init__.py`（`guard()`）、`app/auth.py`（`current_staff()` 改为查 `staff` 表并校验 `status='A'`）、`app/modules/system.py`（角色/状态变更时清理目标账号会话或提高会话版本号）。

### P2

#### QAD-03　登录 `next` 开放重定向（外站/协议相对/`javascript:`）
- 路由：`POST /login?next=...`
- 复现：
  ```bash
  curl -s -D - -o /dev/null -X POST --data 'login_name=admin01&password=123456' \
    'http://127.0.0.1:8780/login?next=https%3A%2F%2Fevil.example.com%2Fx' | grep -i '^location'
  # 实际 Location: https://evil.example.com/x
  # next=//evil.example.com/x  → Location: //evil.example.com/x
  # next=javascript:alert(1)   → Location: javascript:alert(1)
  ```
- 期望：只允许站内相对路径；实际：可跳任意外站（钓鱼风险）。
- 建议修复文件：`app/auth.py`（`login()`，与 QAD-01 一并处理）。

#### QAD-04　修改密码成功后提示被 `session.clear()` 清掉
- 路由：`POST /password`
- 复现（用临时账号避免改演示账号密码）：登录任意账号→`POST /password` 提交正确原密码与合规新密码→跟随 302 到登录页，页面**没有**“密码修改成功，请重新登录”提示（`grep '修改成功'` 为 0），但新密码确实生效。
- 原因：`app/auth.py:246-248` 先 `flash(...)` 后 `session.clear()`，把 flash 一起清掉。
- 期望：成功提示可见；建议先 `session.clear()` 再 `flash`，或改为“已退出，请用新密码登录”。
- 建议修复文件：`app/auth.py`（`change_password()`）。

#### QAD-05　赛程状态接口对非法目标状态“静默切换”
- 路由：`POST /schedule/<match_id>/status`，表单 `status=F`（或任意非 N/P 值）
- 复现：
  ```bash
  curl -s -c /tmp/qa_sched.txt -d 'login_name=schedule01&password=123456' http://127.0.0.1:8780/login -o /dev/null
  MID=$(mysql -h 127.0.0.1 -P 3307 -u root --protocol=TCP -N china_open -e "select match_id from \`match\` where status='P' limit 1")
  curl -s -b /tmp/qa_sched.txt -d 'status=F' -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8780/schedule/$MID/status   # 302
  mysql ... -e "select status from \`match\` where match_id='$MID'"   # 实测 P→N（被静默切换）
  ```
- 原因：`app/modules/schedule.py:353-355` 只接受 `target in ('N','P')`，其余值退回“按当前状态取反”，导致传 F 反而执行 P→N。
- 期望：非法目标值给出中文错误提示且状态不变。
- 建议修复文件：`app/modules/schedule.py`（`change_status()`）。

#### QAD-06　删除已结束比赛不回退胜负场次，统计与选手数据不一致
- 路由：`POST /schedule/<match_id>/delete`（对 `status='F'` 的比赛）
- 复现：新建一场比赛→`/results/<id>/start`→录分（2:0）→核对双方 `win_count/loss_count`→`POST /schedule/<id>/delete`→再查：胜方 `win_count` 不回退，而统计页“已结束场次”减 1、“选手累计胜场”仍包含该场。
- 实测：`win 1->1, loss 1->1, 已结束场次 18->17`；统计报表与选手档案数据自此不一致。
- 期望：要么禁止删除已结束比赛（历史成绩保护），要么在同一事务中回退双方胜负场次并重算。
- 建议修复文件：`app/modules/schedule.py`（`delete()`）、`app/modules/results.py`（胜负统计口径）。

#### QAD-07　`back` 参数含 CRLF → 500，且写操作已提交（与 500 页/报告 4.7 的“已回滚”声明不符）
- 路由：`POST /schedule/<id>/status`、`POST /schedule/<id>/delete`（表单字段 `back`，`schedule._back_url()` 只校验前缀 `/schedule/`）
- 复现：
  ```bash
  MID=$(mysql ... -N china_open -e "select match_id from \`match\` where status='N' limit 1")
  curl -s -b /tmp/qa_sched.txt --data-urlencode $'back=/schedule/\r\nX-Test: y' \
    -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8780/schedule/$MID/status"   # 实测 500
  mysql ... -e "select status from \`match\` where match_id='$MID'"                  # 已从 N 变为 P（写操作已提交）
  ```
  同理对 `/schedule/<id>/delete` 带 CRLF 的 `back` 会 500，但比赛（含级联 match_player）**已被删除**。
- 期望：非法 `back` 回落到 `url_for`；任何 500 都不应留下已提交的业务写操作（或 500 页不要声称“事务已回滚”）。
- 建议修复文件：`app/modules/schedule.py`（`_back_url()` 增加白名单/字符校验；涉及的多步写操作用 `db.transaction()` 包裹）。

#### QAD-08　CSV 公式注入（选手名以 `=` 开头原样导出）
- 路由：`GET /schedule/export`（统计 CSV 同样拼接姓名文本）
- 复现：新增运动员姓名 `=SUM(1+1)xx` → 为它编排一场比赛 → 导出该日赛程 CSV → 单元格内容以 `=SUM(1+1)` 开头；Excel 打开会当公式执行。
- 实测：`含以 = 开头的单元格: ['=SUM(1+1)09']`。
- 期望：导出时对以 `= + - @` 开头的单元格加前缀（如 `'`）或转义。
- 建议修复文件：`app/modules/schedule.py`（`export_csv()`）、`app/modules/results.py`（`export_stats()`）。

#### QAD-09　登录失败提示暴露账号是否存在（用户枚举）
- 路由：`POST /login`
- 复现：对 `admin01` 连续输错密码 → 提示“账号或密码错误，还可尝试 2 次。”；对不存在的账号输错 → 提示“账号或密码错误。”（无尝试次数）。
- 期望：两种场景提示一致；实际可用该差异批量探测有效账号。
- 建议修复文件：`app/auth.py`（`login()` 失败分支）。

#### QAD-10　写操作无 CSRF 防护；会话 Cookie 未显式设置 SameSite
- 路由：所有 POST（示例 `POST /system/staff/create`）
- 复现：
  ```bash
  curl -s -c /tmp/qa_c.txt -d 'login_name=admin01&password=123456' http://127.0.0.1:8780/login -o /dev/null
  curl -s -b /tmp/qa_c.txt -H 'Origin: https://evil.example.com' -H 'Referer: https://evil.example.com/x' \
    -d 'name=CSRF&post=x&login_name=csrf_test&password=123456&role=recorder' \
    http://127.0.0.1:8780/system/staff/create -o /dev/null
  mysql ... -e "select count(*) from staff where login_name='csrf_test'"   # 1，跨站来源写入成功
  # 登录响应 Set-Cookie 形如：session=...; HttpOnly; Path=/（无 SameSite 属性）
  ```
- 风险说明：Flask 默认 `SESSION_COOKIE_SAMESITE=None`，现代浏览器对无 SameSite 的 Cookie 默认按 Lax 处理，跨站表单 POST 通常带不上 Cookie，实际被利用概率降低；但服务端缺 Origin/CSRF 校验，安全设计（报告 4.7）未覆盖此项，建议加固。
- 期望：写操作校验 Origin/Referer 或引入 CSRF token；`config.py` 显式 `SESSION_COOKIE_SAMESITE='Lax'`、`SESSION_COOKIE_SECURE`（按部署）。
- 建议修复文件：`app/__init__.py`（before_request）、`config.py`、各写操作表单/视图。

#### QAD-11　默认以 Flask 调试模式启动，`.env.example` 未提供 `APP_DEBUG`
- 路由/配置：`config.py:73` `DEBUG = _get('APP_DEBUG', '1')`；`.env.example` 无 `APP_DEBUG` 与 `SESSION_TIMEOUT_MIN`。按 README“三步启动”（复制 `.env.example` 为 `.env`）操作后 `create_app()` 的 DEBUG 实测为 `True`，500 时浏览器展示 Werkzeug 调试器/堆栈，与报告 4.7“系统异常统一友好提示”不符（结合 QAD-01/QAD-07 可直接被触发）。
- 复现：`python3 -c "from app import create_app; print(create_app('config').config['DEBUG'])"` → `True`。
- 建议修复文件：`config.py`（默认 `'0'`）、`.env.example`（补 `APP_DEBUG=0`、`SESSION_TIMEOUT_MIN=30`）、`README.md` 运行说明。

#### QAD-12　赛程保存失败时把数据库异常原文提示给用户
- 路由：`POST /schedule/create`
- 复现：`curl -b /tmp/qa_sched.txt --data-urlencode 'team1_players=P0000001'$'\x00' ...`（或任意能穿过表单校验、在写 `match_player` 时触发 `Data too long` 的输入）；页面 flash 出现 `保存失败：(1406, "Data too long for column 'player_id' at row 1")`。
- 原因：`app/modules/schedule.py:217` `flash('保存失败：%s' % exc, 'danger')`。
- 期望：用户看中文友好提示，异常只写日志。
- 建议修复文件：`app/modules/schedule.py`（`create()` 的 except 分支）。

#### QAD-13　成绩录入页选中已结束比赛时仍渲染录分表单，无“成绩已锁定”提示
- 路由：`GET /results/?match_id=<已结束比赛>`
- 实际：页面正常渲染表单，提交后才提示“成绩已锁定”；期望：页面直接显示锁定说明并禁用表单。
- 建议修复文件：`app/modules/results.py`（`index()`/`_render_index()`）、`templates/results/index.html`。

#### QAD-14　演示数据漂移：README/seed 声明与库内实际状态不一致
- 现状：`sql/02_seed.sql` 注释与 README “二、数据规模”声明 **已结束 16 / 进行中 4 / 未开始 16**；本次审查前快照为 **F 17 / P 3 / N 16**（自建 e2e 复跑后会进一步变成 F18/P2）。
- 原因：`tests/test_e2e.py` 会真实结束一场 P 比赛并累加胜负场次，脚本清理只删自建数据、不还原被改状态；README 只写“脚本会自建测试数据并在结束时清理”，未提示会改动演示数据。
- 影响：答辩前若不复位，README/演示脚本里的“进行中 4 场”等描述与界面不一致；不影响主流程演示。
- 建议修复：README 增加告警与“跑完 e2e 用 `tools/init_db.py` 复位”的说明；或让 e2e 记录并还原被改比赛（P→原状态、胜负场次回退）。本报告已在结束时把库还原到审查前快照。

### 误报排除（不计入缺陷）

- `500.html` 虽渲染 `{{ error }}`，但实测渲染的是通用 500 文案（`500 Internal Server Error: ...`），**未泄露堆栈/SQL**，不构成缺陷；`db_error.html` 仅在数据库连接失败时显示原始连接错误（不含密码，含 host/user），属提示级信息，建议改为固定文案。
- 46 项边界用例（空值/201 字超长/负数/非法日期/非法枚举/`' OR '1'='1`/emoji/XSS/路径穿越/500KB 请求体）均未 500；所有含 SQL 关键字与引号的输入均按参数化安全存储，XSS 在内部端与观众端均被 Jinja 自动转义。

---

## 四、文档不一致清单

> 以审查时最新版 `README.md`（09:46 更新）、`CONTRACT.md`、`sql/*.sql`、`app/validation.py`、`docs/系统实现说明.md`、`docs/答辩演示脚本.md` 为准。

| 编号 | 文档位置 | 文档描述 | 实际实现/事实 | 建议 |
| --- | --- | --- | --- | --- |
| DOC-01 | README.md:43（二、数据规模） | 比赛“已结束 16 / 进行中 4 / 未开始 16” | 审查前库为 17/3/16；`test_e2e.py` 跑完为 18/2/16；`02_seed.sql` 声明 16/4/16 | 与 QAD-14 一并处理：复位数据或修正文档 |
| DOC-02 | README.md:48、65 | 标题“三步启动”实际列 4 步（含“进入目录”）；line 65 的地址 `\*\*<http://127.0.0.1:5000\*\*：>` 加粗/链接语法错位 | 按 1—4 步执行可启动；URL 渲染异常 | 改为“四步启动”/“启动步骤”并修正 URL 语法 |
| DOC-03 | README.md:10、72-76；docs/答辩演示脚本.md:27 | 模块1 角色仅写“系统管理员”；演示账号菜单写 schedule01“工作台、基础数据、赛程编排”、record01“工作台、成绩录入、统计报表”；答辩脚本称 record01 “左侧菜单只剩成绩管理” | “登录/退出/修改密码”对所有角色开放；两个账号实际菜单还包含“观众查询端”（工作台为上级菜单），并非“只剩成绩管理” | 模块1 拆出“登录/退出/修改密码（全部角色）”；补充观众端菜单 |
| DOC-04 | `.env.example` | 未包含 `APP_DEBUG`、`SESSION_TIMEOUT_MIN` | `config.py` 支持这两项且 `APP_DEBUG` 默认 `'1'`，按 README 启动即为调试模式（见 QAD-11） | 补齐两项并明确建议 `APP_DEBUG=0` |
| DOC-05 | sql/01_schema.sql:36 | 注释“员工编号，S+3位流水号” | `tools/init_db.py` 与 `services.next_serial('staff','staff_id','S',4)` 实际生成 `S0001`（S+4 位） | 改注释为“S+4 位流水号” |
| DOC-06 | app/validation.py:8；docs/答辩演示脚本.md:95 | “BR-04 …不再接受修改（管理员更正另计）”“更正由管理员操作并留痕” | 系统没有任何管理员更正成绩的入口/接口，任何角色对 F 比赛录分一律被拒 | 删除“管理员更正”表述，或后续实现该入口并在文档中注明 |
| DOC-07 | README.md 七（差异说明）；报告 2.1.3/2.2/2.4/4.5/4.6/5.2/5.4 | 报告承诺：Excel/CSV 导入名单；管理员数据备份、数据库每日自动备份；参赛表“约束一场比赛只能有 2 或 4 条记录”；统计报表“按场地的胜负分布图”；裁判与场地同页“左表场地右表裁判” | 均未实现：无导入入口；无备份功能/脚本（仅口头说可用 mysqldump）；2/4 人仅表单校验、数据库无该约束；统计为表格无图表；裁判/场地拆成两个页面（CONTRACT 允许“也可以分页做”）；README 差异表也未列出这些差异；另 `match.created_at/updated_at`、`staff.created_at` 等新增字段未在差异表说明 | 在 README 七补列差异，或在报告中把上述内容删减/标注为后续改进 |

---

## 五、未覆盖风险与建议

1. **未执行 `tools/init_db.py`**（任务约束：会清空共享演示库）。README“三步启动”中的建库/导入演示数据流程只做了代码静态核对（`split_sql` 注释/引号处理），未在全新空库上端到端实测；建议 Lead 在最终交付前用独立库（如 `china_open_qa`）跑一次 `python tools/init_db.py --database china_open_qa`。
2. **未做真实浏览器渲染与兼容性验证**（Chrome/Edge、1366×768）：只验证了 HTML 返回、静态资源 200 与 `docs/截图/` 存在，未逐页核对截图与当前代码渲染是否一致。
3. **未做并发/性能实测**：报告 2.4 的“40 人并发、普通页面 ≤2 秒、赛程筛选 ≤3 秒”未压测；唯一键并发兜底路径未在并发下验证（单请求下冲突校验先于唯一键，未触发数据库唯一键报错）。
4. **CSRF 仅用 Origin 头/测试客户端模拟**：真实浏览器行为受 SameSite 默认策略影响（见 QAD-10 风险说明），未做跨站真实浏览器验证。
5. **跨天比赛时间模型未覆盖**：`23:59` 开始 + 120 分钟的“结束时间”显示为 `01:59`，冲突校验按当日分钟数比较，不检测跨天重叠（当前演示数据无此类比赛，未定为缺陷，建议加限制或文档说明）。
6. **数据备份/恢复流程未验证**：报告 2.2/2.4/5.4 要求的自动备份未实现（见 DOC-07），本次仅用 `mysqldump` 做人工备份/还原。
7. **未覆盖报告承诺但无入口的功能**：Excel/CSV 导入、管理员更正成绩、备份管理界面（均无可测入口）。
8. **极端数据库状态未测**：表被清空/视图丢失/绕过应用直接写库等场景下的页面健壮性，以及 MySQL 断连时的 `db_error` 页面表现（仅静态检查了处理器存在）。
9. **既有测试脚本的副作用**：`tests/test_e2e.py` 会改演示数据且不还原（QAD-14），建议改造后再作为答辩前自检脚本。

---

## 附一、测试数据与还原

- 审查前备份：`mysqldump -h 127.0.0.1 -P 3307 -u root --protocol=TCP --databases china_open > /tmp/qa_backup_china_open.sql`（09:38 快照）
- 还原脚本：`/tmp/qa_restore.sh`（`mysql ... < /tmp/qa_backup_china_open.sql`）
- 审查结束已还原并复核：player 40 / referee 12 / court 10 / match 36 / match_player 108 / staff 3 / sys_log 20；状态分布 F17 / P3 / N16（与审查前一致；与 seed 声明的 16/4/16 存在既有差异，见 QAD-14）。
- 审查期间通过 HTTP 创建的比赛/运动员/裁判/场地/账号均已随还原清除；未执行 `tools/init_db.py`，未改动业务代码。

## 附二、实际执行的关键命令

```bash
cd "/Users/ryanhu/Desktop/大学课程/大三上/管理信息系统/管理信息系统分析与设计/china_open_mis"

# 1) 既有基线（会改演示库状态，跑前已备份、跑后已还原）
PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= python3 tests/test_e2e.py
#   → 测试结果：49/49 通过，0 失败（证据 /tmp/qa_e2e_baseline.txt）

# 2) 真实服务（curl 验证，APP_DEBUG=0 模拟非调试运行）
PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= APP_PORT=8780 APP_DEBUG=0 python3 app.py
curl -s -o /dev/null -w 'public=%{http_code}\n'   http://127.0.0.1:8780/public/          # 200
curl -s -o /dev/null -w 'root=%{http_code}\n'     http://127.0.0.1:8780/                 # 302 → /public/
curl -s -o /dev/null -w 'static=%{http_code}\n'   http://127.0.0.1:8780/static/vendor/bootstrap/bootstrap.min.css  # 200
curl -s -c /tmp/c.txt -d 'login_name=record01&password=123456' -o /dev/null -w 'login=%{http_code}\n' \
     http://127.0.0.1:8780/login                                                        # 302
curl -s -b /tmp/c.txt -o /dev/null -w 'recorder_schedule=%{http_code}\n' http://127.0.0.1:8780/schedule/  # 403（TC-08）
curl -s -o /dev/null -w 'anon_dashboard=%{http_code}\n' http://127.0.0.1:8780/dashboard/               # 302 → /login?next=/dashboard/

# 3) QA 自建 251 项独立黑盒检查（Flask test_client + 直接查库）
PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= python3 /tmp/qa_harness.py
#   → SUMMARY: 230/251 PASS, 21 FAIL；证据 /tmp/qa_final.txt、/tmp/qa_results.json

# 4) 边界/一致性补充探针
PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= python3 /tmp/qa_probe.py
#   → 时长/日期/时间/排名/容量/比分边界均通过；删除已结束比赛胜负不回退、CSV 公式注入 2 项失败

# 5) 关键缺陷复现（登录 CRLF 500 与 back CRLF 500，真实服务实测）
curl -s -o /dev/null -w '%{http_code}\n' -X POST --data 'login_name=admin01&password=123456' \
  'http://127.0.0.1:8780/login?next=%2Ffoo%0d%0aX-Test%3A%20y'                          # 500
MID=$(mysql -h 127.0.0.1 -P 3307 -u root --protocol=TCP -N china_open -e "select match_id from \`match\` where status='N' limit 1")
curl -s -c /tmp/qa_sched.txt -d 'login_name=schedule01&password=123456' http://127.0.0.1:8780/login -o /dev/null
curl -s -b /tmp/qa_sched.txt --data-urlencode $'back=/schedule/\r\nX-Test: y' \
  -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8780/schedule/$MID/status"            # 500

# 6) 备份 / 还原
mysqldump -h 127.0.0.1 -P 3307 -u root --protocol=TCP --databases china_open > /tmp/qa_backup_china_open.sql
bash /tmp/qa_restore.sh
mysql -h 127.0.0.1 -P 3307 -u root --protocol=TCP china_open -e \
  "select (select count(*) from player) players,(select count(*) from referee) refs,(select count(*) from court) courts,(select count(*) from \`match\`) matches,(select count(*) from match_player) mp,(select count(*) from staff) staff,(select count(*) from sys_log) logs; select status,count(*) from \`match\` group by status;"
```

## 附三、S0—S11 独立检查结果分布

| 分节 | 内容 | 通过/总数 |
| --- | --- | --- |
| S0 | 环境与数据基线 | 3/3 |
| S1 | FR-01/FR-02 登录、菜单、账号、日志、会话超时 | 34/35 |
| S2 | FR-03～FR-06 基础数据 | 25/25 |
| S3 | FR-07/FR-08 赛程编排与三类冲突 | 24/24 |
| S4 | FR-10/FR-11 成绩录入、胜负场次、统计 | 25/25 |
| S5 | FR-09 筛选与 CSV 导出 | 13/13 |
| S6 | FR-12 观众端只读 | 14/14 |
| S7 | 权限矩阵（3 角色 × 10 路由 + 越权写 + 菜单链接） | 17/17 |
| S8 | 边界与异常输入（46 项） | 46/46 |
| S9 | 数据完整性 / 状态机（1 项偏差） | 12/13 |
| S10 | 导出细节、静态离线、安全与文档核对 | 15/23 |
| S11 | 缺陷确认专项（安全/一致性） | 2/13 |
| **合计** | | **230/251** |

---

## 六、缺陷修复记录（Lead 处理后补记）

> 说明：以下为本报告提交后 Lead 的修复结果，由 Lead 用同一套复现命令回归验证（2026-11-20 09:53—09:57）。

| 编号 | 等级 | 处理结果 | 修复位置 |
| --- | --- | --- | --- |
| QAD-01 | P1 | **已修复**：新增 `auth.safe_next_url()`，`next` 只接受本站相对路径，拒绝 CRLF/控制字符/反斜杠；CRLF 请求返回 302 不再 500 | `app/auth.py` |
| QAD-02 | P1 | **已修复**：`before_request` 每次请求从 `staff` 表核对账号状态与角色，停用/降权立即清会话并要求重新登录，旧会话不能再越权写库 | `app/__init__.py` |
| QAD-03 | P2 | **已修复**：同 QAD-01，外站/协议相对/`javascript:` 地址统一回落到工作台 | `app/auth.py` |
| QAD-04 | P2 | **已修复**：改为先 `session.clear()` 再 `flash()`，成功提示可见 | `app/auth.py` |
| QAD-05 | P2 | **已修复**：显式传入非法目标状态时给出中文提示且状态不变（不再静默取反） | `app/modules/schedule.py` |
| QAD-06 | P2 | **已修复**：禁止删除已结束比赛（成绩锁定，保护历史成绩与统计口径），提示中文原因 | `app/modules/schedule.py` |
| QAD-07 | P2 | **已修复**：`back` 参数增加控制字符/反斜杠/空格校验，非法值回落到 `url_for`，不再 500 | `app/modules/schedule.py` |
| QAD-08 | P2 | **已修复**：新增 `services.csv_safe()`，赛程 CSV 与统计 CSV 对 `= + - @` 开头的单元格加前导单引号 | `app/services.py`、`schedule.py`、`results.py` |
| QAD-09 | P2 | **已修复**：失败提示统一为"账号或密码错误。"（不再暴露是否还可尝试次数） | `app/auth.py` |
| QAD-10 | P2 | **已修复（加固级）**：POST 增加 Origin/Referer 同源校验（跨站来源返回 403）；`SESSION_COOKIE_SAMESITE=Lax`、`HTTPONLY=True`。未引入完整 CSRF token，属课程设计规模下的折中 | `app/__init__.py`、`config.py` |
| QAD-11 | P2 | **已修复**：`APP_DEBUG` 默认改为 `0`；`.env.example` 补 `APP_DEBUG=0`、`SESSION_TIMEOUT_MIN=30` | `config.py`、`.env.example` |
| QAD-12 | P2 | **已修复**：数据库异常只写日志，页面给中文友好提示 | `app/modules/schedule.py` |
| QAD-13 | P2 | **复核为误报**：`/results/?match_id=<F 比赛>` 实际已渲染"成绩已锁定"提示且不显示录分表单（Lead 回归脚本 `'成绩已锁定' in body and 'name="set_score"' not in body` 通过）；本次仅顺手修掉了模板里"请联系系统管理员更正"的不实表述 | `app/templates/results/index.html` |
| QAD-14 | P2 | **已修复**：`tests/test_e2e.py` 增加录分前快照、结束时还原比赛状态与选手胜负场次；复跑后状态分布保持 F16/P4/N16 | `tests/test_e2e.py` |
| DOC-01~07 | — | **已修复**：README 启动步骤/URL/角色菜单/数据规模说明，`.env.example` 字段，schema 注释（S+4），validation 中"管理员更正"表述，差异表补列未实现项与新增字段 | `README.md`、`.env.example`、`sql/01_schema.sql`、`app/validation.py`、`docs/答辩演示脚本.md` |

回归验证命令与结果（Lead 执行）：

```bash
cd china_open_mis
# 1) 既有 49 项 e2e（含数据还原）
PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= python3 tests/test_e2e.py
#   → 49/49 通过；跑前跑后比赛状态分布均为 F16/P4/N16

# 2) 缺陷复现回归（全部 PASS）
#   login next CRLF → 302 且无换行；next=https://evil/、//evil/、javascript: → 均回落到站内
#   降权/停用后旧会话 → 302 重新登录，越权写库 0 条
#   status=F → 拒绝且状态保持 P；back CRLF → 302 无 500；删除 F 比赛 → 拒绝
#   CSV 含 '=SUM(1+1)xx' → 导出为 "'=SUM(1+1)xx"；Origin: evil → 403 且未写库
#   create_app().config['DEBUG'] → False；Set-Cookie 含 SameSite=Lax

# 3) 全新库建库验证
PYTHONPATH=/tmp/pydeps DB_PORT=3307 DB_PASSWORD= python3 tools/init_db.py --database china_open_qa
#   → 8 个对象（7 表 + 1 视图），player 40 / match 36 / match_player 108，无报错（验证后已 drop 该测试库）
```
