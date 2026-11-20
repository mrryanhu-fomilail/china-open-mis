# coding: utf-8
"""
业务规则校验（报告 2.5 节业务规则、4.3 节冲突校验、5.3 节测试用例）

    BR-01 同一名运动员在同一时间段内不能出现在两场比赛中
    BR-02 同一片场地在同一时间段内只能安排一场比赛；维护中的场地不参与排期
    BR-03 每场比赛只能指定一名主裁判，裁判排班状态不能是停用
    BR-04 比赛成绩录入后状态置为已结束，此后不再接受任何比分修改

时间区间判定：两场比赛 [开始, 开始+预计时长) 有交集才算冲突，
前一场结束时间与下一场开始时间正好相同不算冲突（测试用例 TC-04）。
"""
import re
from datetime import date, datetime

from flask import current_app

from . import db, services

TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
SET_RE = re.compile(r'^([0-3]):([0-3])$')
GAME_RE = re.compile(r'^(\d{1,2}:\d{1,2})( \d{1,2}:\d{1,2}){0,2}$')

SINGLES_EVENTS = {'MS', 'WS'}
DOUBLES_EVENTS = {'MD', 'WD', 'XD'}


# ---------------------------------------------------------------------------
# 基础校验工具
# ---------------------------------------------------------------------------
def intervals_overlap(start_a, dur_a, start_b, dur_b):
    """判断两个时间区间是否重叠（首尾相接不算重叠）。"""
    a1 = services.to_minutes(start_a)
    a2 = a1 + int(dur_a or 0)
    b1 = services.to_minutes(start_b)
    b2 = b1 + int(dur_b or 0)
    return a1 < b2 and b1 < a2


def parse_iso_date(value):
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _conflict(kind, title, message, match_id=None, suggestion=''):
    return {
        'kind': kind,          # player / court / referee / field
        'title': title,
        'message': message,
        'match_id': match_id,
        'suggestion': suggestion,
    }


# ---------------------------------------------------------------------------
# 表单校验（输入类异常，对应 4.7 节）
# ---------------------------------------------------------------------------
def validate_schedule_form(form):
    """校验赛程编排表单，返回 (data, errors)。

    data 中的 players 为 [(player_id, team_no), ...]
    errors 为 {字段名: 错误说明}
    """
    errors = {}
    data = {}

    event_code = (form.get('event_code') or '').strip()
    round_code = (form.get('round_code') or '').strip()
    court_id = (form.get('court_id') or '').strip()
    referee_id = (form.get('referee_id') or '').strip()
    match_date = (form.get('match_date') or '').strip()
    start_time = (form.get('start_time') or '').strip()
    duration_raw = (form.get('duration_min') or '').strip()

    if event_code not in services.EVENT_NAMES:
        errors['event_code'] = '请选择比赛项目。'
    if round_code not in services.ROUND_NAMES:
        errors['round_code'] = '请选择比赛轮次。'
    if not court_id:
        errors['court_id'] = '请选择比赛场地。'
    elif not db.query_one('SELECT court_id FROM court WHERE court_id = %s', (court_id,)):
        errors['court_id'] = '所选场地不存在。'
    if not referee_id:
        errors['referee_id'] = '请选择主裁判。'
    elif not db.query_one('SELECT referee_id FROM referee WHERE referee_id = %s', (referee_id,)):
        errors['referee_id'] = '所选裁判不存在。'

    day = parse_iso_date(match_date)
    if day is None:
        errors['match_date'] = '比赛日期格式应为 YYYY-MM-DD。'
    elif not services.in_event_period(day):
        errors['match_date'] = '比赛日期须在赛事周期 %s 至 %s 内。' % (
            current_app.config['EVENT_START'], current_app.config['EVENT_END'])
    if not TIME_RE.match(start_time):
        errors['start_time'] = '开始时间格式应为 HH:MM（24 小时制）。'

    duration_min = 120
    if duration_raw:
        if not duration_raw.isdigit() or not (30 <= int(duration_raw) <= 300):
            errors['duration_min'] = '预计时长应为 30—300 之间的整数分钟。'
        else:
            duration_min = int(duration_raw)

    # 对阵选手：单打每队 1 人，双打每队 2 人
    team1 = [x.strip() for x in (form.get('team1_players') or '').split(',') if x.strip()]
    team2 = [x.strip() for x in (form.get('team2_players') or '').split(',') if x.strip()]
    players = [(pid, 1) for pid in team1] + [(pid, 2) for pid in team2]
    need = 1 if event_code in SINGLES_EVENTS else 2
    if len(team1) != need or len(team2) != need:
        errors['players'] = '%s需要每方 %d 名选手，请从运动员列表中勾选。' % (
            services.event_name(event_code) or '该单项', need)
    elif len({p[0] for p in players}) != len(players):
        errors['players'] = '对阵双方的选手不能重复。'
    else:
        # 项目与性别约束：男单/男双限男性，女单/女双限女性，混双不做限制
        expected_gender = {'MS': 'M', 'MD': 'M', 'WS': 'F', 'WD': 'F'}.get(event_code)
        for pid, _team in players:
            row = db.query_one('SELECT player_id, name_cn, gender FROM player WHERE player_id = %s',
                               (pid,))
            if row is None:
                errors['players'] = '选手 %s 不存在，请重新选择。' % pid
                break
            if expected_gender and row['gender'] != expected_gender:
                errors['players'] = '%s只能选择%s运动员，%s不符合项目要求。' % (
                    services.event_name(event_code),
                    '男性' if expected_gender == 'M' else '女性',
                    row['name_cn'])
                break

    data.update({
        'event_code': event_code,
        'round_code': round_code,
        'court_id': court_id,
        'referee_id': referee_id,
        'match_date': match_date,
        'start_time': start_time,
        'duration_min': duration_min,
        'players': players,
    })
    return data, errors


# ---------------------------------------------------------------------------
# 业务校验：三类冲突（4.3 节表 7）
# ---------------------------------------------------------------------------
def _day_matches(day, exclude_match_id=None):
    """取出某一天已有的比赛（含参赛选手），用于冲突比对。"""
    sql = ('SELECT match_id, start_time, duration_min, court_id, referee_id, status '
           'FROM `match` WHERE match_date = %s')
    params = [day]
    if exclude_match_id:
        sql += ' AND match_id <> %s'
        params.append(exclude_match_id)
    rows = db.query_all(sql, tuple(params))
    if not rows:
        return []
    pmap = services.fetch_players([r['match_id'] for r in rows])
    for row in rows:
        row['players'] = pmap.get(row['match_id'], [])
    return rows


def check_conflicts(match_date, start_time, duration_min, court_id, referee_id,
                    players, exclude_match_id=None):
    """执行 BR-01 / BR-02 / BR-03 三项校验。

    players: [(player_id, team_no), ...]
    返回冲突列表；空列表表示校验通过。
    """
    conflicts = []
    day = match_date if isinstance(match_date, date) else parse_iso_date(match_date)
    if day is None:
        return conflicts

    # ---- 场地状态：维护中的场地不参与排期（BR-02 / TC-03） ----
    court = db.query_one('SELECT court_id, court_name, status FROM court WHERE court_id = %s',
                         (court_id,))
    if court and court['status'] == 'M':
        conflicts.append(_conflict(
            'court', '场地维护中',
            '场地 %s（%s）当前处于维护中，不参与排期。' % (court['court_name'], court_id),
            suggestion='请选择其他可用场地。'))

    # ---- 裁判排班状态（BR-03） ----
    referee = db.query_one('SELECT referee_id, name, duty_status FROM referee WHERE referee_id = %s',
                           (referee_id,))
    if referee and referee['duty_status'] == 'C':
        conflicts.append(_conflict(
            'referee', '裁判已停用',
            '裁判 %s（%s）的排班状态为停用，不能执裁。' % (referee['name'], referee_id),
            suggestion='请改选可排或已排状态的裁判。'))

    day_rows = _day_matches(day, exclude_match_id)
    new_player_ids = {p[0] for p in players}
    durations = int(duration_min or 0)

    # ---- 选手时间冲突（BR-01） ----
    hit_players = {}
    for row in day_rows:
        if not intervals_overlap(start_time, durations, row['start_time'], row['duration_min']):
            continue
        for p in row['players']:
            if p['player_id'] in new_player_ids:
                hit_players.setdefault(p['player_id'], {
                    'name': p['name_cn'], 'match_id': row['match_id'],
                    'start': row['start_time'],
                    'end': services.end_time(row['start_time'], row['duration_min']),
                })
    for pid, hit in hit_players.items():
        conflicts.append(_conflict(
            'player', '选手时间冲突',
            '选手 %s（%s）在 %s %s-%s 已有比赛 %s，与本场比赛时间重叠。'
            % (hit['name'], pid, day.isoformat(), hit['start'], hit['end'], hit['match_id']),
            match_id=hit['match_id'],
            suggestion='请调整对阵选手或比赛时间。'))

    # ---- 场地时段冲突（BR-02） ----
    for row in day_rows:
        if row['court_id'] != court_id:
            continue
        if intervals_overlap(start_time, durations, row['start_time'], row['duration_min']):
            conflicts.append(_conflict(
                'court', '场地时段冲突',
                '场地 %s 在 %s %s-%s 已安排比赛 %s。'
                % (court['court_name'] if court else court_id, day.isoformat(),
                   row['start_time'], services.end_time(row['start_time'], row['duration_min']),
                   row['match_id']),
                match_id=row['match_id'],
                suggestion='请更换场地或调整开始时间，可用场地见下方候选清单。'))

    # ---- 裁判执裁冲突（BR-03） ----
    for row in day_rows:
        if row['referee_id'] != referee_id:
            continue
        if intervals_overlap(start_time, durations, row['start_time'], row['duration_min']):
            conflicts.append(_conflict(
                'referee', '裁判执裁冲突',
                '裁判 %s 在 %s %s-%s 已有执裁任务 %s。'
                % (referee['name'] if referee else referee_id, day.isoformat(),
                   row['start_time'], services.end_time(row['start_time'], row['duration_min']),
                   row['match_id']),
                match_id=row['match_id'],
                suggestion='请改选其他可排裁判。'))

    return conflicts


def scan_conflicts():
    """全量扫描现有赛程中的冲突，用于工作台"待处理冲突数"。"""
    rows = db.query_all(
        "SELECT match_id, match_date, start_time, duration_min, court_id, referee_id "
        "FROM `match` WHERE status <> 'F' ORDER BY match_date, start_time")
    if not rows:
        return []
    pmap = services.fetch_players([r['match_id'] for r in rows])
    for row in rows:
        row['players'] = pmap.get(row['match_id'], [])

    by_day = {}
    for row in rows:
        by_day.setdefault(row['match_date'], []).append(row)

    conflicts = []
    for day, day_rows in by_day.items():
        for i in range(len(day_rows)):
            for j in range(i + 1, len(day_rows)):
                a, b = day_rows[i], day_rows[j]
                if not intervals_overlap(a['start_time'], a['duration_min'],
                                         b['start_time'], b['duration_min']):
                    continue
                if a['court_id'] == b['court_id']:
                    conflicts.append({'type': '场地', 'a': a['match_id'], 'b': b['match_id'],
                                      'date': str(day)})
                if a['referee_id'] == b['referee_id']:
                    conflicts.append({'type': '裁判', 'a': a['match_id'], 'b': b['match_id'],
                                      'date': str(day)})
                shared = {p['player_id'] for p in a['players']} & {p['player_id'] for p in b['players']}
                if shared:
                    conflicts.append({'type': '选手', 'a': a['match_id'], 'b': b['match_id'],
                                      'date': str(day), 'players': sorted(shared)})
    return conflicts


# ---------------------------------------------------------------------------
# 成绩校验（BR-04 / TC-05 / TC-06）
# ---------------------------------------------------------------------------
def validate_score(form):
    """校验比分表单，返回 (data, errors)。"""
    errors = {}
    set_score = (form.get('set_score') or '').strip()
    game_score = (form.get('game_score') or '').strip()
    actual_raw = (form.get('actual_min') or '').strip()

    if not set_score:
        errors['set_score'] = '盘分不能为空，格式如 2:1。'
    elif not SET_RE.match(set_score):
        errors['set_score'] = '盘分格式不合法，应形如 2:1（每方为 0—3 的整数）。'
    else:
        a, b = [int(x) for x in set_score.split(':')]
        if max(a, b) != 2 or min(a, b) > 1:
            errors['set_score'] = '盘分不合法：网球三盘两胜制，胜方必须为 2 盘，负方为 0 或 1 盘。'

    if not game_score:
        errors['game_score'] = '局分明细不能为空，格式如 6:4 3:6 7:5。'
    elif not GAME_RE.match(game_score):
        errors['game_score'] = '局分明细格式不合法，只能是 数字:数字，以空格分隔、最多三盘。'
    elif len(game_score) > 11:
        errors['game_score'] = '局分明细长度超出 11 个字符，请精简空格。'
    else:
        parts = game_score.split(' ')
        expected = sum(int(x) for x in set_score.split(':')) if SET_RE.match(set_score) else None
        if expected is not None and len(parts) != expected:
            errors['game_score'] = '局分明细为 %d 盘，与盘分 %s 不一致。' % (len(parts), set_score)

    actual_min = None
    if actual_raw:
        if not actual_raw.isdigit() or not (10 <= int(actual_raw) <= 600):
            errors['actual_min'] = '实际时长应为 10—600 之间的整数分钟。'
        else:
            actual_min = int(actual_raw)

    return {'set_score': set_score, 'game_score': game_score, 'actual_min': actual_min}, errors


def can_enter_score(match_row):
    """BR-04：状态为进行中才可录分；已结束不可重复录入。"""
    if match_row is None:
        return False, '比赛不存在。'
    if match_row['status'] == 'F':
        return False, '该场比赛成绩已锁定（状态：已结束），不能重复录入。'
    if match_row['status'] == 'N':
        return False, '该场比赛尚未开始，请先点击"开始比赛"将状态置为进行中。'
    return True, ''


# ---------------------------------------------------------------------------
# 主裁判/选手候选清单（供冲突提示使用）
# ---------------------------------------------------------------------------
def suggest_available_courts(day, start_time, duration_min, exclude_match_id=None):
    """返回该时段空闲且可用的场地清单（TC-03 提示用）。"""
    day = day if isinstance(day, date) else parse_iso_date(day)
    busy = set()
    for row in _day_matches(day, exclude_match_id):
        if intervals_overlap(start_time, duration_min, row['start_time'], row['duration_min']):
            busy.add(row['court_id'])
    return [c for c in services.list_courts(available_only=True) if c['court_id'] not in busy]


def suggest_available_referees(day, start_time, duration_min, exclude_match_id=None):
    day = day if isinstance(day, date) else parse_iso_date(day)
    busy = set()
    for row in _day_matches(day, exclude_match_id):
        if intervals_overlap(start_time, duration_min, row['start_time'], row['duration_min']):
            busy.add(row['referee_id'])
    return [r for r in services.list_referees(available_only=True) if r['referee_id'] not in busy]
