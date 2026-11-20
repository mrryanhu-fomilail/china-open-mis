# coding: utf-8
"""
模块3 赛程编排管理（本系统核心模块）

对应课程设计报告：
    4.3 节 赛程编排处理流程
    2.5 节 BR-01 / BR-02 / BR-03 三类冲突规则（具体校验逻辑在 app/validation.py）
    4.6 节 界面清单：表 11 赛程编排页、赛程表页
    5.3 节 测试用例 TC-01 ~ TC-04

页面与端点（端点名不可改，菜单与工作台已引用）：
    GET  /schedule/                  schedule.index       编排表单 + 当日赛程
    POST /schedule/create            schedule.create      校验并保存比赛（事务）
    GET  /schedule/list              schedule.list_all    赛程表（筛选 + 删除 + 状态切换）
    POST /schedule/<id>/delete       schedule.delete      删除比赛并留痕
    POST /schedule/<id>/status       schedule.change_status 未开始 <-> 进行中
    GET  /schedule/export            schedule.export_csv  导出当前筛选结果为 CSV（UTF-8 BOM）

设计要点：
    1. 表单校验与冲突校验全部调用 app/validation.py，本模块不另写一套规则；
    2. 冲突时保留表单内容，并给出候选场地 / 候选裁判建议；
    3. match 与 match_player 的写入放在同一个 db.transaction() 内，失败整体回滚；
    4. 删除、状态变更写操作日志（2.4 节要求）。
"""
import csv
import io
from datetime import datetime
from urllib.parse import quote

from flask import (Blueprint, Response, current_app, flash, redirect,
                   render_template, request, url_for)

from .. import auth, db, services, validation

bp = Blueprint('schedule', __name__)

# 表单字段 -> 中文名称，用于把 validation 返回的 errors 逐条展示成中文
FIELD_LABELS = {
    'event_code': '比赛项目',
    'round_code': '比赛轮次',
    'court_id': '比赛场地',
    'referee_id': '主裁判',
    'match_date': '比赛日期',
    'start_time': '开始时间',
    'duration_min': '预计时长',
    'players': '对阵选手',
}

# CSV 导出列（中文表头，对应报告表 9 比赛信息）
CSV_HEADERS = [
    '比赛编号', '比赛日期', '开始时间', '结束时间', '预计时长(分钟)',
    '场地编号', '场地名称', '项目', '轮次', '主裁判',
    '一方选手', '另一方选手', '状态', '盘分', '局分明细', '实际时长(分钟)',
]


# ---------------------------------------------------------------------------
# 表单 / 日期辅助
# ---------------------------------------------------------------------------
def _empty_form(day=None):
    """新建比赛时的默认表单值。"""
    return {
        'event_code': '',
        'round_code': '',
        'court_id': '',
        'referee_id': '',
        'match_date': day or services.default_show_date(),
        'start_time': '10:00',
        'duration_min': '120',
        'team1_players': '',
        'team2_players': '',
    }


def _form_snapshot(form):
    """把提交的表单原样保存下来，校验失败时用于回填（表 4 异常流程）。"""
    return {
        'event_code': form.get('event_code') or '',
        'round_code': form.get('round_code') or '',
        'court_id': form.get('court_id') or '',
        'referee_id': form.get('referee_id') or '',
        'match_date': form.get('match_date') or '',
        'start_time': form.get('start_time') or '',
        'duration_min': form.get('duration_min') or '',
        'team1_players': form.get('team1_players') or '',
        'team2_players': form.get('team2_players') or '',
    }


def _normalize_day(value):
    """把任意日期输入规范成 YYYY-MM-DD，非法或为空时用演示默认日期。"""
    day = validation.parse_iso_date(value) if value else None
    if day is None:
        return services.default_show_date()
    return day.isoformat()


def _parse_filters():
    """解析赛程表的筛选参数（列表页与 CSV 导出共用，保证导出内容与列表一致）。"""
    filters = {
        'date': (request.args.get('date') or '').strip(),
        'court_id': (request.args.get('court_id') or '').strip(),
        'event_code': (request.args.get('event_code') or '').strip(),
        'status': (request.args.get('status') or '').strip(),
        'keyword': (request.args.get('keyword') or '').strip(),
    }
    # 过滤明显非法的筛选值，避免把脏参数带进 SQL
    if filters['date'] and validation.parse_iso_date(filters['date']) is None:
        filters['date'] = ''
    if filters['court_id'] and not db.query_one(
            'SELECT court_id FROM court WHERE court_id = %s', (filters['court_id'],)):
        filters['court_id'] = ''
    if filters['event_code'] not in services.EVENT_NAMES:
        filters['event_code'] = ''
    if filters['status'] not in services.MATCH_STATUS:
        filters['status'] = ''
    return filters


def _back_url(default_endpoint, **values):
    """操作完成后的返回地址。

    只接受本站 /schedule/ 下的相对路径，并拒绝控制字符与反斜杠，
    避免开放重定向与 HTTP 头注入（CRLF）导致的 500（QAD-07）。
    """
    back = (request.form.get('back') or '').strip()
    if back.startswith('/schedule/') and not any(
            ch in back for ch in ('\r', '\n', '\x00', '\t', '\\', ' ')):
        return back
    return url_for(default_endpoint, **values)


# ---------------------------------------------------------------------------
# 页面：编排表单 + 当日赛程
# ---------------------------------------------------------------------------
def _render_index(day=None, form=None, errors=None, conflicts=None,
                  suggest_courts=None, suggest_referees=None):
    """渲染编排页（正常访问与保存失败复用同一渲染逻辑，便于表单回填）。"""
    day = day or services.default_show_date()
    form = form or _empty_form(day)

    players = services.list_players()
    male_players = [p for p in players if p['gender'] == 'M']
    female_players = [p for p in players if p['gender'] == 'F']

    return render_template(
        'schedule/index.html',
        day=day,
        form=form,
        errors=errors or {},
        field_labels=FIELD_LABELS,
        conflicts=conflicts or [],
        suggest_courts=suggest_courts or [],
        suggest_referees=suggest_referees or [],
        players=male_players + female_players,
        male_players=male_players,
        female_players=female_players,
        # FR-06：场地下拉只列出可用场地；裁判下拉排除停用裁判
        courts=services.list_courts(available_only=True),
        referees=services.list_referees(available_only=True),
        matches=services.list_matches(date=day),
    )


@bp.route('/')
@auth.require_roles('admin', 'scheduler')
def index():
    """赛程编排页：上半区编排表单，下半区当前日期赛程。"""
    return _render_index(day=_normalize_day(request.args.get('date')))


@bp.route('/create', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def create():
    """保存新比赛：表单校验 -> 三类冲突校验 -> 事务写入 match / match_player。"""
    form = _form_snapshot(request.form)
    data, errors = validation.validate_schedule_form(request.form)

    # 保留用户填写的比赛日期，方便重新渲染时下半区仍显示该日赛程
    day = _normalize_day(form['match_date'])

    # 第一层：输入类异常（必填、格式、项目与选手性别等）
    if errors:
        flash('表单校验未通过，请按提示修正后重新提交。', 'danger')
        return _render_index(day=day, form=form, errors=errors)

    # 第二层：BR-01 选手时间冲突 / BR-02 场地时段与维护 / BR-03 裁判执裁与停用
    conflicts = validation.check_conflicts(
        data['match_date'], data['start_time'], data['duration_min'],
        data['court_id'], data['referee_id'], data['players'],
    )
    if conflicts:
        suggest_courts = validation.suggest_available_courts(
            data['match_date'], data['start_time'], data['duration_min'])
        suggest_referees = validation.suggest_available_referees(
            data['match_date'], data['start_time'], data['duration_min'])
        flash('检测到 %d 项冲突，比赛未保存，请调整后重试。' % len(conflicts), 'danger')
        return _render_index(
            day=day, form=form, conflicts=conflicts,
            suggest_courts=suggest_courts, suggest_referees=suggest_referees)

    # 第三层：无冲突，写入比赛与参赛记录（同一事务，任一步失败整体回滚）
    match_id = services.next_match_id()
    try:
        with db.transaction() as cur:
            cur.execute(
                'INSERT INTO `match` (match_id, event_code, round_code, court_id, '
                'referee_id, match_date, start_time, duration_min, status) '
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'N')",
                (match_id, data['event_code'], data['round_code'], data['court_id'],
                 data['referee_id'], data['match_date'], data['start_time'],
                 data['duration_min']),
            )
            for player_id, team_no in data['players']:
                cur.execute(
                    'INSERT INTO match_player (match_id, player_id, team_no) '
                    'VALUES (%s, %s, %s)',
                    (match_id, player_id, team_no),
                )
    except Exception as exc:  # 唯一键冲突等数据库异常统一友好提示
        current_app.logger.exception('保存比赛失败：%s', exc)
        flash('保存失败：该时间段该场地可能已被占用，或输入数据超出允许长度，'
              '请检查后重试（详细原因已记入后台日志）。', 'danger')
        return _render_index(day=day, form=form)

    court = db.query_one('SELECT court_name FROM court WHERE court_id = %s',
                         (data['court_id'],))
    referee = db.query_one('SELECT name FROM referee WHERE referee_id = %s',
                           (data['referee_id'],))
    team_text = ' / '.join([pid for pid, _ in data['players']])
    auth.log_operation(
        '新增', 'match:' + match_id,
        '编排比赛 %s：%s %s %s %s-%s 场地 %s 裁判 %s 选手 %s'
        % (match_id, services.event_name(data['event_code']),
           services.round_name(data['round_code']), data['match_date'],
           data['start_time'],
           services.end_time(data['start_time'], data['duration_min']),
           (court or {}).get('court_name', data['court_id']),
           (referee or {}).get('name', data['referee_id']), team_text),
    )
    flash('比赛 %s 编排成功：%s %s · %s %s-%s · %s · 主裁判 %s。'
          % (match_id, services.event_name(data['event_code']),
             services.round_name(data['round_code']), data['match_date'],
             data['start_time'],
             services.end_time(data['start_time'], data['duration_min']),
             (court or {}).get('court_name', data['court_id']),
             (referee or {}).get('name', data['referee_id'])),
          'success')
    return redirect(url_for('schedule.index', date=data['match_date']))


# ---------------------------------------------------------------------------
# 页面：赛程表（筛选 + 删除 + 状态切换 + 导出）
# ---------------------------------------------------------------------------
@bp.route('/list')
@auth.require_roles('admin', 'scheduler')
def list_all():
    """赛程表：按日期 / 场地 / 项目 / 状态 / 关键字筛选。"""
    filters = _parse_filters()
    matches = services.list_matches(**filters)

    status_counts = {'N': 0, 'P': 0, 'F': 0}
    for match in matches:
        if match['status'] in status_counts:
            status_counts[match['status']] += 1

    active_filters = {key: value for key, value in filters.items() if value}
    return render_template(
        'schedule/list.html',
        matches=matches,
        filters=filters,
        status_counts=status_counts,
        courts=services.list_courts(),
        export_url=url_for('schedule.export_csv', **active_filters),
        back_url=url_for('schedule.list_all', **active_filters),
    )


def _csv_row(values):
    """CSV 行：对以 = + - @ 开头的单元格做公式注入防护（QAD-08）。"""
    return [services.csv_safe(value) for value in values]


@bp.route('/export')
@auth.require_roles('admin', 'scheduler')
def export_csv():
    """导出当前筛选结果为 CSV：UTF-8 带 BOM，Excel 直接打开不乱码，文件名带导出日期。"""
    filters = _parse_filters()
    matches = services.list_matches(**filters)

    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADERS)
    for match in matches:
        writer.writerow(_csv_row([
            match['match_id'],
            match['match_date'],
            match['start_time'],
            services.end_time(match['start_time'], match['duration_min']),
            match['duration_min'],
            match['court_id'],
            match['court_name'],
            match['event_name'],
            match['round_name'],
            match['referee_name'],
            match['team1_text'],
            match['team2_text'],
            match['status_name'],
            match['set_score'] or '',
            match['game_score'] or '',
            match['actual_min'] if match['actual_min'] is not None else '',
        ]))

    stamp = datetime.now().strftime('%Y%m%d')
    ascii_name = 'schedule_%s.csv' % stamp
    utf8_name = quote('中网赛程_%s.csv' % stamp)
    payload = buffer.getvalue().encode('utf-8-sig')
    response = Response(payload, content_type='text/csv; charset=utf-8')
    response.headers['Content-Disposition'] = (
        "attachment; filename=%s; filename*=UTF-8''%s" % (ascii_name, utf8_name))
    return response


# ---------------------------------------------------------------------------
# 写操作：删除比赛 / 状态切换
# ---------------------------------------------------------------------------
@bp.route('/<match_id>/delete', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def delete(match_id):
    """删除比赛：match_player 由外键 ON DELETE CASCADE 级联删除，并写操作日志。"""
    match = services.get_match(match_id)
    if match is None:
        flash('比赛 %s 不存在或已被删除。' % match_id, 'warning')
        return redirect(_back_url('schedule.list_all'))

    if match['status'] == 'F':
        flash('比赛 %s 已结束，成绩已锁定（BR-04），不能删除，以保护历史成绩与统计口径。'
              % match_id, 'warning')
        return redirect(_back_url('schedule.list_all'))

    db.execute('DELETE FROM `match` WHERE match_id = %s', (match_id,))
    auth.log_operation(
        '删除', 'match:' + match_id,
        '删除比赛 %s：%s %s %s %s-%s 场地 %s'
        % (match_id, match['event_name'], match['round_name'], match['match_date'],
           match['start_time'], match['start_end'].split('-')[-1],
           match['court_name']),
    )
    flash('比赛 %s 已删除（参赛记录已级联清理）。' % match_id, 'success')
    return redirect(_back_url('schedule.list_all'))


@bp.route('/<match_id>/status', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def change_status(match_id):
    """比赛状态切换：未开始 <-> 进行中；已结束的比赛成绩已锁定，不允许改回。"""
    match = db.query_one('SELECT match_id, status FROM `match` WHERE match_id = %s',
                         (match_id,))
    if match is None:
        flash('比赛 %s 不存在或已被删除。' % match_id, 'warning')
        return redirect(_back_url('schedule.list_all'))

    old_status = match['status']
    if old_status == 'F':
        flash('比赛 %s 已结束，成绩已锁定，不能再修改状态。' % match_id, 'warning')
        return redirect(_back_url('schedule.list_all'))

    # 允许前端显式指定目标状态；显式传入非法值时给出提示并保持不变（QAD-05）
    target = (request.form.get('status') or '').strip()
    if target and target not in ('N', 'P'):
        flash('比赛 %s 的目标状态不合法（只允许 未开始 / 进行中），状态保持不变。' % match_id,
              'danger')
        return redirect(_back_url('schedule.list_all'))
    if not target:
        target = 'P' if old_status == 'N' else 'N'
    db.execute('UPDATE `match` SET status = %s WHERE match_id = %s',
               (target, match_id))
    auth.log_operation(
        '状态变更', 'match:' + match_id,
        '比赛状态 %s -> %s（%s）'
        % (services.status_name(old_status), services.status_name(target), match_id),
    )
    flash('比赛 %s 状态已更新：%s → %s。'
          % (match_id, services.status_name(old_status),
             services.status_name(target)), 'success')
    return redirect(_back_url('schedule.list_all'))
