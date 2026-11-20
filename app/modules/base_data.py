# coding: utf-8
"""
模块2 基础数据管理：运动员档案 / 裁判档案 / 场地档案（报告 4.6 节表 11）。

对应需求：
    FR-03 基础数据维护（新增、修改、删除、状态变更）
    BR-02 维护中的场地不参与排期（状态切换时提示受影响的未结束比赛）
    BR-03 裁判排班状态为"停用"时不能执裁
    2.4 节 删除与状态变更写入操作日志（sys_log）

删除保护规则：
    运动员：存在未结束比赛（status <> 'F'）时拒绝删除并列出关联比赛编号；
            存在已结束比赛引用时也拒绝删除（保护历史成绩数据，避免外键报错）。
    裁判  ：被任意比赛引用时拒绝删除（match.referee_id 外键）。
    场地  ：被任意比赛引用时拒绝删除（match.court_id 外键）。

写权限：admin / scheduler（蓝图级权限已在 app/__init__.py 校验，写操作视图再校验一次）。
"""
import re

import pymysql
from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import auth, db, services

bp = Blueprint('base_data', __name__)

# 裁判级别 / 场地类型的选项（与报告数据字典一致，模板下拉共用）
REFEREE_LEVELS = ['金牌', '银牌', '铜牌', '国际级']
COURT_TYPES = ['中心场', '副场', '外围场', '训练场']

# 国籍代码：2—3 位拉丁字母（数据库 CHAR(3)，如 CHN / USA）
NATION_RE = re.compile(r'^[A-Z]{2,3}$')

PLAYER_LIST_ENDPOINT = 'base_data.player_list'
REFEREE_LIST_ENDPOINT = 'base_data.referee_list'
COURT_LIST_ENDPOINT = 'base_data.court_list'


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _redirect(endpoint):
    """写操作结束后统一回到列表页。"""
    return redirect(url_for(endpoint))


def _form_value(form, name, default=''):
    """取表单值并去掉首尾空白（None 视为空串）。"""
    value = form.get(name)
    if value is None:
        return default
    return value.strip()


def _int_query_arg(name):
    """读取整数类型的查询参数：空或非法时返回 None（非法值由页面直接忽略）。"""
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _flash_errors(errors, prefix='保存失败：'):
    """把后端校验错误拼成一条中文提示（4.7 节输入类异常统一提示）。"""
    messages = [msg for msg in errors.values() if msg]
    if messages:
        flash(prefix + ' '.join(messages), 'danger')


def _count_map(sql, key='ref_id'):
    """执行 GROUP BY 计数查询，返回 {编号: 数量}，用于列表页显示引用次数。"""
    return {row[key]: row['c'] for row in db.query_all(sql)}


# ---------------------------------------------------------------------------
# 表单校验：运动员
# ---------------------------------------------------------------------------
def validate_player_form(form):
    """校验运动员表单，返回 (data, errors)；errors 为空表示通过（前端 HTML5 之外的后端校验）。"""
    errors = {}
    data = {
        'player_id': _form_value(form, 'player_id'),
        'name_cn': _form_value(form, 'name_cn'),
        'name_en': _form_value(form, 'name_en') or None,
        'nation': _form_value(form, 'nation').upper(),
        'gender': _form_value(form, 'gender').upper(),
        'rank_single': None,
        'rank_double': None,
        'association': _form_value(form, 'association') or None,
        'wildcard': _form_value(form, 'wildcard', 'N').upper() or 'N',
    }

    if not data['name_cn']:
        errors['name_cn'] = '请填写运动员姓名。'
    elif len(data['name_cn']) > 30:
        errors['name_cn'] = '姓名长度不能超过 30 个字符。'
    if len(data['name_en'] or '') > 50:
        errors['name_en'] = '外文姓名长度不能超过 50 个字符。'

    if not data['nation']:
        errors['nation'] = '请填写国籍代码。'
    elif not NATION_RE.match(data['nation']):
        errors['nation'] = '国籍代码应为 2—3 位英文字母，如 CHN、USA。'

    if data['gender'] not in ('M', 'F'):
        errors['gender'] = '性别必须为男（M）或女（F）。'

    rank_single_raw = _form_value(form, 'rank_single')
    if not rank_single_raw:
        errors['rank_single'] = '请填写单打世界排名，无排名请填 0。'
    elif not rank_single_raw.isdigit() or not (0 <= int(rank_single_raw) <= 2000):
        errors['rank_single'] = '单打世界排名应为 0—2000 之间的整数。'
    else:
        data['rank_single'] = int(rank_single_raw)

    rank_double_raw = _form_value(form, 'rank_double')
    if rank_double_raw:
        if not rank_double_raw.isdigit() or not (0 <= int(rank_double_raw) <= 2000):
            errors['rank_double'] = '双打世界排名应为 0—2000 之间的整数或留空。'
        else:
            data['rank_double'] = int(rank_double_raw)

    if data['association'] and len(data['association']) > 20:
        errors['association'] = '所属协会长度不能超过 20 个字符。'
    if data['wildcard'] not in ('Y', 'N'):
        errors['wildcard'] = '外卡标记只能为 Y（持外卡）或 N（正常晋级）。'

    return data, errors


def validate_referee_form(form):
    """校验裁判表单，返回 (data, errors)。排班状态由独立端点维护，这里不含状态。"""
    errors = {}
    data = {
        'referee_id': _form_value(form, 'referee_id'),
        'name': _form_value(form, 'name'),
        'level': _form_value(form, 'level'),
        'cert_level': _form_value(form, 'cert_level') or None,
    }
    if not data['name']:
        errors['name'] = '请填写裁判姓名。'
    elif len(data['name']) > 30:
        errors['name'] = '裁判姓名长度不能超过 30 个字符。'
    if data['level'] not in REFEREE_LEVELS:
        errors['level'] = '请选择裁判级别（%s）。' % '、'.join(REFEREE_LEVELS)
    if data['cert_level'] and len(data['cert_level']) > 20:
        errors['cert_level'] = '持证等级长度不能超过 20 个字符。'
    return data, errors


def validate_court_form(form):
    """校验场地表单，返回 (data, errors)。场地状态由独立端点维护，这里不含状态。"""
    errors = {}
    data = {
        'court_id': _form_value(form, 'court_id'),
        'court_name': _form_value(form, 'court_name'),
        'court_type': _form_value(form, 'court_type'),
        'capacity': None,
    }
    if not data['court_name']:
        errors['court_name'] = '请填写场地名称。'
    elif len(data['court_name']) > 30:
        errors['court_name'] = '场地名称长度不能超过 30 个字符。'
    if data['court_type'] not in COURT_TYPES:
        errors['court_type'] = '请选择场地类型（%s）。' % '、'.join(COURT_TYPES)
    capacity_raw = _form_value(form, 'capacity')
    if not capacity_raw:
        errors['capacity'] = '请填写场地容纳人数。'
    elif not capacity_raw.isdigit() or not (0 <= int(capacity_raw) <= 200000):
        errors['capacity'] = '容纳人数应为 0—200000 之间的整数。'
    else:
        data['capacity'] = int(capacity_raw)
    return data, errors


# ===========================================================================
# 运动员档案
# ===========================================================================
@bp.route('/player')
@auth.require_roles('admin', 'scheduler')
def player_list():
    """运动员列表：按国籍 / 性别 / 排名区间 / 关键字（姓名或编号）组合检索。"""
    nation = (request.args.get('nation') or '').strip().upper()
    gender = (request.args.get('gender') or '').strip().upper()
    keyword = (request.args.get('keyword') or '').strip()
    rank_min = _int_query_arg('rank_min')
    rank_max = _int_query_arg('rank_max')

    if gender and gender not in ('M', 'F'):
        gender = ''
    if rank_min is not None and rank_max is not None and rank_min > rank_max:
        rank_min, rank_max = rank_max, rank_min
        flash('排名区间起止值已自动对调。', 'warning')

    players = services.list_players(
        nation=nation or None,
        gender=gender or None,
        rank_min=rank_min,
        rank_max=rank_max,
        keyword=keyword or None,
    )
    stats = db.query_one(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(gender = 'M'), 0) AS male, "
        "COALESCE(SUM(gender = 'F'), 0) AS female, "
        "COALESCE(SUM(wildcard = 'Y'), 0) AS wildcard "
        "FROM player"
    )
    match_counts = _count_map(
        'SELECT player_id AS ref_id, COUNT(*) AS c FROM match_player GROUP BY player_id')
    return render_template(
        'base_data/player.html',
        players=players,
        stats=stats,
        match_counts=match_counts,
        nations=services.list_nations(),
        filters={
            'nation': nation,
            'gender': gender,
            'keyword': keyword,
            'rank_min': request.args.get('rank_min', ''),
            'rank_max': request.args.get('rank_max', ''),
        },
    )


@bp.route('/player/save', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def player_save():
    """新增或修改运动员（隐藏字段 player_id 为空 = 新增）。"""
    data, errors = validate_player_form(request.form)
    if errors:
        _flash_errors(errors)
        return _redirect(PLAYER_LIST_ENDPOINT)

    player_id = data['player_id']
    params = (
        data['name_cn'], data['name_en'], data['nation'], data['gender'],
        data['rank_single'], data['rank_double'], data['association'], data['wildcard'],
    )

    if player_id:
        # ---------------- 修改 ----------------
        row = db.query_one('SELECT player_id, name_cn FROM player WHERE player_id = %s',
                           (player_id,))
        if row is None:
            flash('要修改的运动员不存在，可能已被删除。', 'warning')
            return _redirect(PLAYER_LIST_ENDPOINT)
        db.execute(
            'UPDATE player SET name_cn = %s, name_en = %s, nation = %s, gender = %s, '
            'rank_single = %s, rank_double = %s, association = %s, wildcard = %s '
            'WHERE player_id = %s',
            params + (player_id,),
        )
        auth.log_operation('修改', 'player:' + player_id,
                           '修改运动员 %s（%s）档案' % (data['name_cn'], player_id))
        flash('运动员 %s（%s）的资料已保存。' % (data['name_cn'], player_id), 'success')
    else:
        # ---------------- 新增 ----------------
        new_id = services.next_serial('player', 'player_id', 'P', 7)
        db.execute(
            'INSERT INTO player (player_id, name_cn, name_en, nation, gender, '
            'rank_single, rank_double, association, wildcard) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (new_id,) + params,
        )
        auth.log_operation('新增', 'player:' + new_id,
                           '新增运动员 %s（编号 %s）' % (data['name_cn'], new_id))
        flash('运动员 %s 新增成功，编号为 %s。' % (data['name_cn'], new_id), 'success')
    return _redirect(PLAYER_LIST_ENDPOINT)


@bp.route('/player/<player_id>/delete', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def player_delete(player_id):
    """删除运动员：存在未结束比赛时拒绝并列出关联比赛编号（FR-03）。"""
    row = db.query_one('SELECT player_id, name_cn FROM player WHERE player_id = %s',
                       (player_id,))
    if row is None:
        flash('运动员不存在或已被删除。', 'warning')
        return _redirect(PLAYER_LIST_ENDPOINT)

    # 1) 未结束比赛：直接拒绝并说明原因（报告表 11 要求列出阻止原因）
    pending = db.query_all(
        'SELECT m.match_id, m.status FROM match_player mp '
        'JOIN `match` m ON m.match_id = mp.match_id '
        "WHERE mp.player_id = %s AND m.status <> 'F' ORDER BY m.match_id",
        (player_id,),
    )
    if pending:
        ids = '、'.join(r['match_id'] for r in pending)
        flash('无法删除运动员 %s（%s）：该运动员还有 %d 场未结束比赛（%s），'
              '请先结束或删除相关比赛后再删除档案。'
              % (row['name_cn'], player_id, len(pending), ids), 'danger')
        return _redirect(PLAYER_LIST_ENDPOINT)

    # 2) 仅被已结束比赛引用：保留历史成绩数据，同样不允许删除
    refs = db.query_all(
        'SELECT match_id FROM match_player WHERE player_id = %s ORDER BY match_id',
        (player_id,),
    )
    if refs:
        ids = '、'.join(r['match_id'] for r in refs)
        flash('无法删除运动员 %s（%s）：该运动员已被 %d 场已结束比赛引用（%s），'
              '删除会破坏历史成绩数据。' % (row['name_cn'], player_id, len(refs), ids),
              'danger')
        return _redirect(PLAYER_LIST_ENDPOINT)

    # 3) 无任何比赛引用，执行删除并留痕
    try:
        db.execute('DELETE FROM player WHERE player_id = %s', (player_id,))
    except pymysql.err.IntegrityError:
        flash('无法删除运动员 %s：该运动员仍被比赛记录引用。' % row['name_cn'], 'danger')
        return _redirect(PLAYER_LIST_ENDPOINT)

    auth.log_operation('删除', 'player:' + player_id,
                       '删除运动员 %s（%s）' % (row['name_cn'], player_id))
    flash('运动员 %s（%s）已删除。' % (row['name_cn'], player_id), 'success')
    return _redirect(PLAYER_LIST_ENDPOINT)


# ===========================================================================
# 裁判档案
# ===========================================================================
@bp.route('/referee')
@auth.require_roles('admin', 'scheduler')
def referee_list():
    """裁判列表：支持关键字（姓名或编号）与排班状态筛选。"""
    status = (request.args.get('status') or '').strip().upper()
    keyword = (request.args.get('keyword') or '').strip()
    if status and status not in services.DUTY_STATUS:
        status = ''

    referees = services.list_referees(status=status or None, keyword=keyword or None)
    stats = db.query_one(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(duty_status = 'A'), 0) AS available, "
        "COALESCE(SUM(duty_status = 'B'), 0) AS scheduled, "
        "COALESCE(SUM(duty_status = 'C'), 0) AS disabled "
        "FROM referee"
    )
    match_counts = _count_map(
        'SELECT referee_id AS ref_id, COUNT(*) AS c FROM `match` GROUP BY referee_id')
    return render_template(
        'base_data/referee.html',
        referees=referees,
        stats=stats,
        match_counts=match_counts,
        levels=REFEREE_LEVELS,
        filters={'status': status, 'keyword': keyword},
    )


@bp.route('/referee/save', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def referee_save():
    """新增或修改裁判（隐藏字段 referee_id 为空 = 新增）。"""
    data, errors = validate_referee_form(request.form)
    if errors:
        _flash_errors(errors)
        return _redirect(REFEREE_LIST_ENDPOINT)

    params = (data['name'], data['level'], data['cert_level'])

    if data['referee_id']:
        row = db.query_one('SELECT referee_id, name FROM referee WHERE referee_id = %s',
                           (data['referee_id'],))
        if row is None:
            flash('要修改的裁判不存在，可能已被删除。', 'warning')
            return _redirect(REFEREE_LIST_ENDPOINT)
        db.execute('UPDATE referee SET name = %s, level = %s, cert_level = %s '
                   'WHERE referee_id = %s', params + (data['referee_id'],))
        auth.log_operation('修改', 'referee:' + data['referee_id'],
                           '修改裁判 %s（%s）档案' % (data['name'], data['referee_id']))
        flash('裁判 %s（%s）的资料已保存。' % (data['name'], data['referee_id']), 'success')
    else:
        new_id = services.next_serial('referee', 'referee_id', 'R', 5)
        db.execute('INSERT INTO referee (referee_id, name, level, cert_level, duty_status) '
                   "VALUES (%s, %s, %s, %s, 'A')", (new_id,) + params)
        auth.log_operation('新增', 'referee:' + new_id,
                           '新增裁判 %s（编号 %s）' % (data['name'], new_id))
        flash('裁判 %s 新增成功，编号为 %s，排班状态默认为"可排"。'
              % (data['name'], new_id), 'success')
    return _redirect(REFEREE_LIST_ENDPOINT)


@bp.route('/referee/<referee_id>/status', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def referee_status(referee_id):
    """切换裁判排班状态：A 可排 / B 已排 / C 停用（写操作日志）。"""
    new_status = (request.form.get('status') or '').strip().upper()
    if new_status not in services.DUTY_STATUS:
        flash('请选择有效的排班状态。', 'danger')
        return _redirect(REFEREE_LIST_ENDPOINT)

    row = db.query_one('SELECT referee_id, name, duty_status FROM referee WHERE referee_id = %s',
                       (referee_id,))
    if row is None:
        flash('裁判不存在或已被删除。', 'warning')
        return _redirect(REFEREE_LIST_ENDPOINT)
    if row['duty_status'] == new_status:
        flash('裁判 %s 的排班状态未发生变化。' % row['name'], 'info')
        return _redirect(REFEREE_LIST_ENDPOINT)

    db.execute('UPDATE referee SET duty_status = %s WHERE referee_id = %s',
               (new_status, referee_id))
    old_name = services.duty_status_name(row['duty_status'])
    new_name = services.duty_status_name(new_status)
    auth.log_operation('状态变更', 'referee:' + referee_id,
                       '裁判排班状态 %s -> %s' % (old_name, new_name))
    flash('裁判 %s（%s）的排班状态已由"%s"改为"%s"。'
          % (row['name'], referee_id, old_name, new_name), 'success')
    return _redirect(REFEREE_LIST_ENDPOINT)


@bp.route('/referee/<referee_id>/delete', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def referee_delete(referee_id):
    """删除裁判：已被比赛引用时拒绝删除。"""
    row = db.query_one('SELECT referee_id, name FROM referee WHERE referee_id = %s',
                       (referee_id,))
    if row is None:
        flash('裁判不存在或已被删除。', 'warning')
        return _redirect(REFEREE_LIST_ENDPOINT)

    refs = db.query_all(
        'SELECT match_id, status FROM `match` WHERE referee_id = %s ORDER BY match_id',
        (referee_id,),
    )
    if refs:
        ids = '、'.join(r['match_id'] for r in refs)
        flash('无法删除裁判 %s（%s）：该裁判已被 %d 场比赛引用（%s），请先调整相关比赛的主裁判。'
              % (row['name'], referee_id, len(refs), ids), 'danger')
        return _redirect(REFEREE_LIST_ENDPOINT)

    try:
        db.execute('DELETE FROM referee WHERE referee_id = %s', (referee_id,))
    except pymysql.err.IntegrityError:
        flash('无法删除裁判 %s：该裁判仍被比赛记录引用。' % row['name'], 'danger')
        return _redirect(REFEREE_LIST_ENDPOINT)

    auth.log_operation('删除', 'referee:' + referee_id,
                       '删除裁判 %s（%s）' % (row['name'], referee_id))
    flash('裁判 %s（%s）已删除。' % (row['name'], referee_id), 'success')
    return _redirect(REFEREE_LIST_ENDPOINT)


# ===========================================================================
# 场地档案
# ===========================================================================
@bp.route('/court')
@auth.require_roles('admin', 'scheduler')
def court_list():
    """场地列表：支持关键字与状态筛选，并统计每片场地未结束的比赛数（改维护提示用）。"""
    status = (request.args.get('status') or '').strip().upper()
    keyword = (request.args.get('keyword') or '').strip()
    if status and status not in services.COURT_STATUS:
        status = ''

    courts = services.list_courts(available_only=(status == 'A'), keyword=keyword or None)
    if status == 'M':
        # list_courts 只支持"仅可用"，维护中在这里过滤（数据量很小）
        courts = [c for c in courts if c['status'] == 'M']

    pending_counts = _count_map(
        "SELECT court_id AS ref_id, COUNT(*) AS c FROM `match` "
        "WHERE status <> 'F' GROUP BY court_id")
    match_counts = _count_map(
        'SELECT court_id AS ref_id, COUNT(*) AS c FROM `match` GROUP BY court_id')
    for court in courts:
        court['pending_count'] = pending_counts.get(court['court_id'], 0)
        court['match_count'] = match_counts.get(court['court_id'], 0)

    stats = db.query_one(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(status = 'A'), 0) AS available, "
        "COALESCE(SUM(status = 'M'), 0) AS maintenance, "
        "COALESCE(SUM(capacity), 0) AS capacity "
        "FROM court"
    )
    return render_template(
        'base_data/court.html',
        courts=courts,
        stats=stats,
        court_types=COURT_TYPES,
        filters={'status': status, 'keyword': keyword},
    )


@bp.route('/court/save', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def court_save():
    """新增或修改场地（隐藏字段 court_id 为空 = 新增，新增场地默认"可用"）。"""
    data, errors = validate_court_form(request.form)
    if errors:
        _flash_errors(errors)
        return _redirect(COURT_LIST_ENDPOINT)

    params = (data['court_name'], data['court_type'], data['capacity'])

    if data['court_id']:
        row = db.query_one('SELECT court_id, court_name FROM court WHERE court_id = %s',
                           (data['court_id'],))
        if row is None:
            flash('要修改的场地不存在，可能已被删除。', 'warning')
            return _redirect(COURT_LIST_ENDPOINT)
        db.execute('UPDATE court SET court_name = %s, court_type = %s, capacity = %s '
                   'WHERE court_id = %s', params + (data['court_id'],))
        auth.log_operation('修改', 'court:' + data['court_id'],
                           '修改场地 %s（%s）档案' % (data['court_name'], data['court_id']))
        flash('场地 %s（%s）的资料已保存。' % (data['court_name'], data['court_id']), 'success')
    else:
        new_id = services.next_serial('court', 'court_id', 'C', 5)
        db.execute('INSERT INTO court (court_id, court_name, court_type, capacity, status) '
                   "VALUES (%s, %s, %s, %s, 'A')", (new_id,) + params)
        auth.log_operation('新增', 'court:' + new_id,
                           '新增场地 %s（编号 %s）' % (data['court_name'], new_id))
        flash('场地 %s 新增成功，编号为 %s，状态默认为"可用"。'
              % (data['court_name'], new_id), 'success')
    return _redirect(COURT_LIST_ENDPOINT)


@bp.route('/court/<court_id>/status', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def court_status(court_id):
    """切换场地状态：A 可用 / M 维护中。

    改为"维护中"时统计该场地未结束比赛数并提示"该场地已有 N 场比赛需要改场"；
    前端在提交前用弹窗二次确认（模板中已带 N 值）。
    """
    new_status = (request.form.get('status') or '').strip().upper()
    if new_status not in services.COURT_STATUS:
        flash('请选择有效的场地状态（可用 / 维护中）。', 'danger')
        return _redirect(COURT_LIST_ENDPOINT)

    row = db.query_one('SELECT court_id, court_name, status FROM court WHERE court_id = %s',
                       (court_id,))
    if row is None:
        flash('场地不存在或已被删除。', 'warning')
        return _redirect(COURT_LIST_ENDPOINT)
    if row['status'] == new_status:
        flash('场地 %s 的状态未发生变化。' % row['court_name'], 'info')
        return _redirect(COURT_LIST_ENDPOINT)

    # BR-02：维护中的场地不参与排期，先统计受影响的未结束比赛
    pending = db.query_value(
        "SELECT COUNT(*) AS c FROM `match` WHERE court_id = %s AND status <> 'F'",
        (court_id,), 0,
    )
    db.execute('UPDATE court SET status = %s WHERE court_id = %s', (new_status, court_id))

    old_name = services.court_status_name(row['status'])
    new_name = services.court_status_name(new_status)
    detail = '场地状态 %s -> %s' % (old_name, new_name)
    if new_status == 'M':
        detail += '，该场地未结束比赛 %d 场需改场' % pending
    auth.log_operation('状态变更', 'court:' + court_id, detail)

    if new_status == 'M' and pending > 0:
        flash('场地 %s（%s）已改为维护中；该场地已有 %d 场比赛需要改场，请到赛程编排中调整。'
              % (row['court_name'], court_id, pending), 'warning')
    elif new_status == 'M':
        flash('场地 %s（%s）已改为维护中，当前没有未结束比赛受影响。'
              % (row['court_name'], court_id), 'success')
    else:
        flash('场地 %s（%s）已恢复为可用。' % (row['court_name'], court_id), 'success')
    return _redirect(COURT_LIST_ENDPOINT)


@bp.route('/court/<court_id>/delete', methods=['POST'])
@auth.require_roles('admin', 'scheduler')
def court_delete(court_id):
    """删除场地：已被比赛引用时拒绝删除。"""
    row = db.query_one('SELECT court_id, court_name FROM court WHERE court_id = %s',
                       (court_id,))
    if row is None:
        flash('场地不存在或已被删除。', 'warning')
        return _redirect(COURT_LIST_ENDPOINT)

    refs = db.query_all(
        'SELECT match_id, status FROM `match` WHERE court_id = %s ORDER BY match_id',
        (court_id,),
    )
    if refs:
        ids = '、'.join(r['match_id'] for r in refs)
        flash('无法删除场地 %s（%s）：该场地已被 %d 场比赛引用（%s），请先调整相关比赛的场地。'
              % (row['court_name'], court_id, len(refs), ids), 'danger')
        return _redirect(COURT_LIST_ENDPOINT)

    try:
        db.execute('DELETE FROM court WHERE court_id = %s', (court_id,))
    except pymysql.err.IntegrityError:
        flash('无法删除场地 %s：该场地仍被比赛记录引用。' % row['court_name'], 'danger')
        return _redirect(COURT_LIST_ENDPOINT)

    auth.log_operation('删除', 'court:' + court_id,
                       '删除场地 %s（%s）' % (row['court_name'], court_id))
    flash('场地 %s（%s）已删除。' % (row['court_name'], court_id), 'success')
    return _redirect(COURT_LIST_ENDPOINT)
