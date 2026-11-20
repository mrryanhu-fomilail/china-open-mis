# coding: utf-8
"""
模块5 观众公开查询（免登录、只读，对应报告 4.6 节界面清单中的"观众查询端"）。

功能：
    1. GET /public/               当日赛程（按场地分组展示对阵、状态、比分）；
    2. GET /public/players        选手资料查询（姓名/编号关键字、国籍、性别筛选）；
    3. GET /public/player/<id>    选手详情 + 参赛记录 + 胜率。

只读约束：
    * 蓝图不注册任何写操作路由，视图内只做 SELECT；
    * 模板中不出现 <form> 提交和任何新增/修改/删除入口（筛选采用链接跳转）。
"""
import re
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, flash, render_template, request

from .. import db, services

bp = Blueprint('public', __name__)

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
GENDER_NAMES = {'M': '男', 'F': '女'}
WEEKDAY_NAMES = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _parse_day(value):
    """把 YYYY-MM-DD 字符串转为 date，非法返回 None。"""
    if not value or not DATE_RE.match(str(value)):
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


def _weekday_label(day):
    """中文星期标签，如 周一。"""
    return WEEKDAY_NAMES[day.weekday()]


def _iter_event_days():
    """赛事周期内的所有日期（供日期选择器使用）。"""
    start = _parse_day(current_app.config['EVENT_START'])
    end = _parse_day(current_app.config['EVENT_END'])
    if start is None or end is None or start > end:
        return []
    days, cursor = [], start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _score_winner_team(set_score):
    """由盘分判断胜方队伍序号：1/2，无法判断返回 None。"""
    try:
        a, b = [int(x) for x in (set_score or '').split(':')]
    except (TypeError, ValueError):
        return None
    if a == b:
        return None
    return 1 if a > b else 2


def _player_rate(player):
    """选手胜率（百分数，保留 1 位小数）；没有参赛记录返回 None。"""
    wins = int(player.get('win_count') or 0)
    losses = int(player.get('loss_count') or 0)
    played = wins + losses
    if played <= 0:
        return None
    return round(wins * 100.0 / played, 1)


# ---------------------------------------------------------------------------
# 当日赛程
# ---------------------------------------------------------------------------
@bp.route('/')
def index():
    """GET /public/：日期选择 + 当日赛程（按场地分组，含比分），无任何编辑入口。"""
    day_raw = (request.args.get('date') or request.args.get('day') or '').strip()
    day = _parse_day(day_raw)
    if day_raw and day is None:
        flash('日期格式不正确（应为 YYYY-MM-DD），已显示默认日期。', 'warning')
    if day is None:
        day = _parse_day(services.default_show_date())
    if day is None:                       # 极端兜底：配置异常时按今天
        day = datetime.now().date()

    day_str = day.isoformat()
    matches = services.list_matches(date=day_str)
    for match in matches:
        # 已结束的比赛高亮胜方队伍，未结束不标注胜负
        match['winner_team'] = (_score_winner_team(match.get('set_score'))
                                if match['status'] == 'F' else None)

    # 按场地分组（中心场、大场地在前），组内按开始时间排序
    match_by_court = {}
    for match in matches:
        match_by_court.setdefault(match['court_id'], []).append(match)
    groups = []
    for court in services.list_courts():
        court_matches = match_by_court.get(court['court_id'])
        if not court_matches:
            continue
        court_matches.sort(key=lambda m: (m['start_time'], m['match_id']))
        groups.append({
            'court_id': court['court_id'],
            'court_name': court['court_name'],
            'court_type': court.get('court_type') or '',
            'capacity': court.get('capacity') or 0,
            'matches': court_matches,
        })

    # 日期选择器：赛事周期内每一天及其比赛场次
    match_count = {day_str: len(matches)}
    for row in db.query_all(
            'SELECT match_date, COUNT(*) AS c FROM `match` '
            'WHERE match_date BETWEEN %s AND %s GROUP BY match_date',
            (current_app.config['EVENT_START'], current_app.config['EVENT_END'])):
        value = row['match_date']
        key = value.isoformat() if hasattr(value, 'isoformat') else str(value)
        match_count[key] = int(row['c'] or 0)
    day_options = []
    for option_day in _iter_event_days():
        key = option_day.isoformat()
        day_options.append({
            'date': key,
            'label': '%04d-%02d-%02d %s' % (option_day.year, option_day.month,
                                            option_day.day, _weekday_label(option_day)),
            'count': match_count.get(key, 0),
        })

    # 前后一天（限制在赛事周期内）
    start = _parse_day(current_app.config['EVENT_START'])
    end = _parse_day(current_app.config['EVENT_END'])
    prev_day = (day - timedelta(days=1)).isoformat() if start and day > start else None
    next_day = (day + timedelta(days=1)).isoformat() if end and day < end else None

    finished_count = sum(1 for m in matches if m['status'] == 'F')
    return render_template(
        'public/index.html',
        day=day_str,
        weekday=_weekday_label(day),
        matches=matches,
        groups=groups,
        finished_count=finished_count,
        day_options=day_options,
        prev_day=prev_day,
        next_day=next_day,
    )


# ---------------------------------------------------------------------------
# 选手资料查询
# ---------------------------------------------------------------------------
@bp.route('/players')
def players():
    """GET /public/players：按姓名/编号关键字、国籍、性别查询选手。"""
    keyword = (request.args.get('keyword') or request.args.get('q') or '').strip()
    nation = (request.args.get('nation') or '').strip()
    gender = (request.args.get('gender') or '').strip()
    if gender not in ('M', 'F'):
        gender = ''

    rows = services.list_players(
        nation=nation or None, gender=gender or None, keyword=keyword or None)
    for row in rows:
        row['played'] = int(row.get('win_count') or 0) + int(row.get('loss_count') or 0)
        row['win_rate'] = _player_rate(row)

    return render_template(
        'public/players.html',
        players=rows,
        keyword=keyword,
        nation=nation,
        gender=gender,
        nations=services.list_nations(),
    )


# ---------------------------------------------------------------------------
# 选手详情 + 参赛记录
# ---------------------------------------------------------------------------
@bp.route('/player/<player_id>')
def player_detail(player_id):
    """GET /public/player/<player_id>：选手资料、参赛记录（对手/比分/结果）与胜率。"""
    player = db.query_one('SELECT * FROM player WHERE player_id = %s', (player_id,))
    if player is None:
        abort(404)

    matches = services.list_matches(
        player_id=player_id, order_by='match_date DESC, start_time DESC')
    wins = int(player.get('win_count') or 0)
    losses = int(player.get('loss_count') or 0)
    played = wins + losses
    win_rate = round(wins * 100.0 / played, 1) if played else None

    for match in matches:
        team1_ids = [p['player_id'] for p in match['team1']]
        my_team = 1 if player_id in team1_ids else 2
        opponents = match['team2'] if my_team == 1 else match['team1']
        match['my_team'] = my_team
        match['opponents'] = opponents
        match['opponent_text'] = ' / '.join(p['name_cn'] for p in opponents) or '待定'
        winner_team = _score_winner_team(match.get('set_score'))
        if match['status'] == 'F' and winner_team:
            if winner_team == my_team:
                match['result'], match['result_badge'] = '胜', 'success'
            else:
                match['result'], match['result_badge'] = '负', 'danger'
        elif match['status'] == 'P':
            match['result'], match['result_badge'] = '进行中', 'warning'
        else:
            match['result'], match['result_badge'] = '待赛', 'secondary'

    return render_template(
        'public/player_detail.html',
        player=player,
        matches=matches,
        wins=wins,
        losses=losses,
        played=played,
        win_rate=win_rate,
        gender_name=GENDER_NAMES.get(player.get('gender'), ''),
    )
