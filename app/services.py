# coding: utf-8
"""
公共查询服务：各业务模块共用的字典、比赛列表查询与展示字段拼装。

所有模块（基础数据 / 赛程编排 / 成绩 / 公开查询 / 工作台）都通过这里
读取数据，避免每个模块各写一套 SQL、显示口径不一致。
"""
from datetime import date, datetime

from flask import current_app

from . import db

# ---------------------------------------------------------------------------
# 枚举字典（数据库存代码，界面显示中文，对应报告 3.6 节数据字典）
# ---------------------------------------------------------------------------
EVENT_NAMES = {
    'MS': '男子单打',
    'WS': '女子单打',
    'MD': '男子双打',
    'WD': '女子双打',
    'XD': '混合双打',
}
ROUND_NAMES = {
    'Q': '资格赛',
    'R1': '首轮',
    'R2': '第二轮',
    'QF': '八强',
    'SF': '四强',
    'F': '决赛',
}
MATCH_STATUS = {'N': '未开始', 'P': '进行中', 'F': '已结束'}
COURT_STATUS = {'A': '可用', 'M': '维护中'}
DUTY_STATUS = {'A': '可排', 'B': '已排', 'C': '停用'}
STAFF_ROLES = {'admin': '系统管理员', 'scheduler': '赛事编排员', 'recorder': '成绩录入员'}
STAFF_STATUS = {'A': '启用', 'D': '停用'}

# 各枚举对应的 Bootstrap 颜色，模板中直接用 badge-xxx
BADGE_COLORS = {
    'N': 'secondary', 'P': 'warning', 'F': 'success',
    'A_court': 'success', 'M_court': 'danger',
    'A_duty': 'success', 'B_duty': 'info', 'C_duty': 'secondary',
}


def event_name(code):
    return EVENT_NAMES.get(code, code or '')


def round_name(code):
    return ROUND_NAMES.get(code, code or '')


def status_name(code):
    return MATCH_STATUS.get(code, code or '')


def court_status_name(code):
    return COURT_STATUS.get(code, code or '')


def duty_status_name(code):
    return DUTY_STATUS.get(code, code or '')


def role_name(code):
    return STAFF_ROLES.get(code, code or '')


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def to_minutes(hhmm):
    """'14:30' -> 870"""
    try:
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])
    except (TypeError, ValueError):
        return 0


def end_time(hhmm, duration_min):
    """开始时间 + 预计时长 -> 结束时间字符串 HH:MM"""
    total = to_minutes(hhmm) + int(duration_min or 0)
    return '%02d:%02d' % (total // 60 % 24, total % 60)


def csv_safe(value):
    """CSV 公式注入防护（QAD-08）。

    Excel/ WPS 会把以 = + - @ 开头的单元格当公式执行，
    导出时统一加一个前导单引号，使其按文本显示。
    """
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


def today_str():
    return date.today().isoformat()


def default_show_date():
    """演示用"当日"：今天落在赛事周期内就用今天，否则取赛程中最早的比赛日。"""
    today = date.today()
    if in_event_period(today):
        return today.isoformat()
    row = db.query_one(
        'SELECT MIN(match_date) AS d FROM `match` '
        'WHERE match_date BETWEEN %s AND %s',
        (current_app.config['EVENT_START'], current_app.config['EVENT_END']),
    )
    if row and row.get('d'):
        value = row['d']
        return value.isoformat() if hasattr(value, 'isoformat') else str(value)
    return current_app.config['EVENT_START']


def in_event_period(day):
    """判断日期是否在赛事周期内（4.5 节：比赛日期须在赛事周期内）。"""
    try:
        if isinstance(day, str):
            day = datetime.strptime(day, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return False
    start = datetime.strptime(current_app.config['EVENT_START'], '%Y-%m-%d').date()
    end = datetime.strptime(current_app.config['EVENT_END'], '%Y-%m-%d').date()
    return start <= day <= end


# ---------------------------------------------------------------------------
# 比赛查询
# ---------------------------------------------------------------------------
_BASE_SQL = """
SELECT match_id, event_code, round_code, court_id, court_name, court_status,
       referee_id, referee_name, match_date, start_time, duration_min,
       set_score, game_score, actual_min, status
FROM v_match_detail
"""


def fetch_players(match_ids):
    """返回 {match_id: [ {player_id, name_cn, name_en, team_no, nation}, ... ]}"""
    if not match_ids:
        return {}
    placeholders = ','.join(['%s'] * len(match_ids))
    rows = db.query_all(
        'SELECT mp.match_id, mp.player_id, mp.team_no, p.name_cn, p.name_en, p.nation, p.gender '
        'FROM match_player mp JOIN player p ON p.player_id = mp.player_id '
        'WHERE mp.match_id IN (%s) ORDER BY mp.match_id, mp.team_no, mp.player_id' % placeholders,
        tuple(match_ids),
    )
    result = {}
    for row in rows:
        result.setdefault(row['match_id'], []).append(row)
    return result


def decorate_match(m, players=None):
    """给比赛记录补上界面需要的展示字段。"""
    if m is None:
        return None
    m = dict(m)
    m['event_name'] = event_name(m['event_code'])
    m['round_name'] = round_name(m['round_code'])
    m['status_name'] = status_name(m['status'])
    m['court_status_name'] = court_status_name(m.get('court_status'))
    m['start_end'] = '%s-%s' % (m['start_time'], end_time(m['start_time'], m['duration_min']))
    if isinstance(m.get('match_date'), (date, datetime)):
        m['match_date'] = m['match_date'].isoformat() if isinstance(m['match_date'], date) else m['match_date'].date().isoformat()

    team1, team2 = [], []
    for p in (players or []):
        (team1 if p['team_no'] == 1 else team2).append(p)
    m['team1'] = team1
    m['team2'] = team2
    m['team1_text'] = ' / '.join(p['name_cn'] for p in team1) or '待定'
    m['team2_text'] = ' / '.join(p['name_cn'] for p in team2) or '待定'
    m['player_ids'] = [p['player_id'] for p in (players or [])]
    return m


def list_matches(date=None, court_id=None, event_code=None, status=None,
                 statuses=None, player_id=None, keyword=None, limit=None,
                 order_by='match_date, start_time'):
    """按条件查询比赛列表（已拼装展示字段）。"""
    sql = _BASE_SQL
    where, params = [], []
    if date:
        where.append('match_date = %s')
        params.append(date)
    if court_id:
        where.append('court_id = %s')
        params.append(court_id)
    if event_code:
        where.append('event_code = %s')
        params.append(event_code)
    if status:
        where.append('status = %s')
        params.append(status)
    if statuses:
        placeholders = ','.join(['%s'] * len(statuses))
        where.append('status IN (%s)' % placeholders)
        params.extend(statuses)
    if player_id:
        where.append('match_id IN (SELECT match_id FROM match_player WHERE player_id = %s)')
        params.append(player_id)
    if keyword:
        like = '%%%s%%' % keyword
        where.append('(match_id LIKE %s OR court_name LIKE %s OR referee_name LIKE %s '
                     'OR match_id IN (SELECT mp.match_id FROM match_player mp JOIN player p '
                     'ON p.player_id = mp.player_id WHERE p.name_cn LIKE %s))')
        params.extend([like, like, like, like])
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY ' + order_by
    if limit:
        sql += ' LIMIT %d' % int(limit)
    rows = db.query_all(sql, tuple(params))
    pmap = fetch_players([r['match_id'] for r in rows])
    return [decorate_match(r, pmap.get(r['match_id'], [])) for r in rows]


def get_match(match_id):
    row = db.query_one(_BASE_SQL + ' WHERE match_id = %s', (match_id,))
    if row is None:
        return None
    pmap = fetch_players([match_id])
    return decorate_match(row, pmap.get(match_id, []))


def next_match_id(year=None):
    """生成比赛编号：M + 年份后两位 + 4 位流水号（报告表 9）。"""
    year = year or datetime.now().year
    prefix = 'M%02d' % (int(year) % 100)
    row = db.query_one(
        'SELECT match_id FROM `match` WHERE match_id LIKE %s ORDER BY match_id DESC LIMIT 1',
        (prefix + '%',),
    )
    seq = 1
    if row and row.get('match_id'):
        try:
            seq = int(row['match_id'][len(prefix):]) + 1
        except ValueError:
            seq = 1
    return '%s%04d' % (prefix, seq)


def next_serial(table, column, prefix, width):
    """通用流水编号生成：prefix + 定长流水号。"""
    row = db.query_one(
        'SELECT `%s` AS code FROM `%s` WHERE `%s` LIKE %%s ORDER BY `%s` DESC LIMIT 1'
        % (column, table, column, column),
        (prefix + '%',),
    )
    seq = 1
    if row and row.get('code'):
        try:
            seq = int(str(row['code'])[len(prefix):]) + 1
        except ValueError:
            seq = 1
    return prefix + str(seq).zfill(width)


# ---------------------------------------------------------------------------
# 基础数据列表
# ---------------------------------------------------------------------------
def list_players(nation=None, gender=None, rank_min=None, rank_max=None,
                 keyword=None, wildcard=None):
    sql = 'SELECT * FROM player'
    where, params = [], []
    if nation:
        where.append('nation = %s')
        params.append(nation)
    if gender:
        where.append('gender = %s')
        params.append(gender)
    if rank_min not in (None, ''):
        where.append('rank_single >= %s')
        params.append(int(rank_min))
    if rank_max not in (None, ''):
        where.append('rank_single <= %s')
        params.append(int(rank_max))
    if wildcard:
        where.append('wildcard = %s')
        params.append(wildcard)
    if keyword:
        like = '%%%s%%' % keyword
        where.append('(player_id LIKE %s OR name_cn LIKE %s OR name_en LIKE %s)')
        params.extend([like, like, like])
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY rank_single = 0, rank_single, player_id'
    return db.query_all(sql, tuple(params))


def list_courts(available_only=False, keyword=None):
    sql = 'SELECT * FROM court'
    where, params = [], []
    if available_only:
        where.append("status = 'A'")
    if keyword:
        where.append('(court_id LIKE %s OR court_name LIKE %s)')
        like = '%%%s%%' % keyword
        params.extend([like, like])
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY capacity DESC, court_id'
    return db.query_all(sql, tuple(params))


def list_referees(status=None, keyword=None, available_only=False):
    sql = 'SELECT * FROM referee'
    where, params = [], []
    if available_only:
        where.append("duty_status <> 'C'")
    elif status:
        where.append('duty_status = %s')
        params.append(status)
    if keyword:
        where.append('(referee_id LIKE %s OR name LIKE %s)')
        like = '%%%s%%' % keyword
        params.extend([like, like])
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY referee_id'
    return db.query_all(sql, tuple(params))


def list_nations():
    rows = db.query_all('SELECT DISTINCT nation FROM player ORDER BY nation')
    return [r['nation'] for r in rows]


# ---------------------------------------------------------------------------
# 工作台统计（4.6 节：当日比赛场次、待处理冲突数、待录比分场次）
# ---------------------------------------------------------------------------
def dashboard_stats(day=None):
    day = day or today_str()
    today_matches = db.query_value(
        'SELECT COUNT(*) AS c FROM `match` WHERE match_date = %s', (day,), 0)
    pending_score = db.query_value(
        "SELECT COUNT(*) AS c FROM `match` WHERE status IN ('N','P')", None, 0)
    finished = db.query_value(
        "SELECT COUNT(*) AS c FROM `match` WHERE status = 'F'", None, 0)
    total_matches = db.query_value('SELECT COUNT(*) AS c FROM `match`', None, 0)
    total_players = db.query_value('SELECT COUNT(*) AS c FROM player', None, 0)
    total_referees = db.query_value('SELECT COUNT(*) AS c FROM referee', None, 0)
    total_courts = db.query_value('SELECT COUNT(*) AS c FROM court', None, 0)
    maintenance_courts = db.query_value(
        "SELECT COUNT(*) AS c FROM court WHERE status = 'M'", None, 0)
    in_progress = db.query_value(
        "SELECT COUNT(*) AS c FROM `match` WHERE status = 'P'", None, 0)
    return {
        'day': day,
        'today_matches': today_matches,
        'pending_score': pending_score,
        'in_progress': in_progress,
        'finished': finished,
        'total_matches': total_matches,
        'total_players': total_players,
        'total_referees': total_referees,
        'total_courts': total_courts,
        'maintenance_courts': maintenance_courts,
    }
