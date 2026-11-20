# coding: utf-8
"""
模块4 成绩管理（FR-09 / FR-10 / FR-11，业务规则 BR-04，报告 4.3 节成绩录入处理流程）。

功能：
    1. 成绩录入页：左侧未结束比赛列表 + 右侧录分表单（盘分 / 局分明细 / 实际时长）；
    2. 开始比赛：未开始（N）-> 进行中（P）；
    3. 保存成绩：校验通过后在一个事务内完成
          写 set_score / game_score / actual_min
          -> 比赛状态置 F
          -> 胜方选手累加 win_count、负方选手累加 loss_count
       任意一步失败整体回滚；
    4. 统计报表：整体概览、按项目 / 场地 / 项目性别分布、选手胜率榜；
    5. 统计报表 CSV 导出（UTF-8 BOM，Excel 直接打开不乱码）。

权限：本蓝图所有视图都要求 admin / recorder（CONTRACT 6.5 节）。
"""
import csv
import io
from datetime import datetime

from flask import (Blueprint, Response, current_app, flash, redirect,
                   render_template, request, url_for)

from .. import auth, db, services, validation

bp = Blueprint('results', __name__)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _score_winner_team(set_score):
    """由盘分（如 2:1）判断胜方队伍序号：1 表示一队胜，2 表示二队胜。"""
    try:
        a, b = [int(x) for x in (set_score or '').split(':')]
    except (TypeError, ValueError):
        return None
    if a == b:
        return None
    return 1 if a > b else 2


def _avg_min(value):
    """平均值统一保留 1 位小数；无数据返回 None。"""
    if value is None:
        return None
    return round(float(value), 1)


def _fmt(value):
    """CSV 中空值输出空字符串。"""
    return '' if value is None else value


def _load_pending_matches():
    """未结束比赛（未开始 + 进行中），按比赛日期、开始时间排序。"""
    return services.list_matches(statuses=['N', 'P'])


def _render_index(match_id=None, selected=None, errors=None, form=None):
    """渲染成绩录入页。

    - 未指定 match_id 时默认选中第一场"进行中"的比赛，方便直接录分；
    - 校验失败时把表单错误与用户输入回填到页面。
    """
    pending = _load_pending_matches()
    if selected is None and not match_id:
        selected_id = next((m['match_id'] for m in pending if m['status'] == 'P'), None)
        if selected_id is None and pending:
            selected_id = pending[0]['match_id']
        if selected_id:
            selected = services.get_match(selected_id)
    return render_template(
        'results/index.html',
        matches=pending,
        selected=selected,
        errors=errors or {},
        form=form,
    )


# ---------------------------------------------------------------------------
# 成绩录入首页
# ---------------------------------------------------------------------------
@bp.route('/')
@auth.require_roles('admin', 'recorder')
def index():
    """GET /results/：左侧未结束比赛列表 + 右侧录分表单。"""
    match_id = (request.args.get('match_id') or '').strip()
    selected = None
    if match_id:
        selected = services.get_match(match_id)
        if selected is None:
            flash('所选比赛 %s 不存在，请从左侧列表重新选择。' % match_id, 'warning')
            match_id = ''
    return _render_index(match_id=match_id, selected=selected)


# ---------------------------------------------------------------------------
# 开始比赛：未开始 -> 进行中
# ---------------------------------------------------------------------------
@bp.route('/<match_id>/start', methods=['POST'])
@auth.require_roles('admin', 'recorder')
def start_match(match_id):
    """POST /results/<match_id>/start：把未开始的比赛置为进行中。"""
    match = services.get_match(match_id)
    if match is None:
        flash('比赛 %s 不存在。' % match_id, 'danger')
        return redirect(url_for('results.index'))
    if match['status'] == 'P':
        flash('比赛 %s 已经处于进行中状态，无需重复开始。' % match_id, 'info')
        return redirect(url_for('results.index', match_id=match_id))
    if match['status'] == 'F':
        flash('比赛 %s 已结束，不能重新开始。' % match_id, 'warning')
        return redirect(url_for('results.index', match_id=match_id))

    db.execute("UPDATE `match` SET status = 'P' WHERE match_id = %s", (match_id,))
    auth.log_operation('状态变更', 'match:' + match_id, '比赛状态 未开始 -> 进行中')
    flash('比赛 %s 已开始，状态置为「进行中」，可以录入比分。' % match_id, 'success')
    return redirect(url_for('results.index', match_id=match_id))


# ---------------------------------------------------------------------------
# 保存比分：一个事务内完成"写比分 + 置 F + 累加胜负场次"
# ---------------------------------------------------------------------------
@bp.route('/<match_id>/score', methods=['POST'])
@auth.require_roles('admin', 'recorder')
def save_score(match_id):
    """POST /results/<match_id>/score：录入比赛成绩。

    处理流程（报告 4.3 节）：
        1. BR-04：只有"进行中"的比赛才能录分（未开始提示先开始、已结束提示锁定）；
        2. 校验盘分 / 局分明细 / 实际时长格式；
        3. 事务内三步写入，任一失败整体回滚；
        4. 成功后写操作日志并提示。
    """
    match = services.get_match(match_id)
    if match is None:
        flash('比赛 %s 不存在。' % match_id, 'danger')
        return redirect(url_for('results.index'))

    # 第 1 步：BR-04 状态校验（FR-10 / TC-05）
    ok, msg = validation.can_enter_score(match)
    if not ok:
        flash(msg, 'warning')
        return redirect(url_for('results.index', match_id=match_id))

    # 第 2 步：比分格式校验（TC-06）
    data, errors = validation.validate_score(request.form)
    if errors:
        flash('成绩录入失败，请按下方红色提示修改后重新提交。', 'danger')
        return _render_index(match_id=match_id, selected=match,
                             errors=errors, form=request.form)

    set_score = data['set_score']
    game_score = data['game_score']
    actual_min = data['actual_min']
    winner_team = _score_winner_team(set_score)

    # 查询双方选手：胜方全部选手加胜场，负方全部选手加负场
    player_rows = db.query_all(
        'SELECT player_id, team_no FROM match_player WHERE match_id = %s '
        'ORDER BY team_no, player_id', (match_id,))
    team1 = [r['player_id'] for r in player_rows if r['team_no'] == 1]
    team2 = [r['player_id'] for r in player_rows if r['team_no'] == 2]
    winners, losers = (team1, team2) if winner_team == 1 else (team2, team1)

    # 第 3 步：三步写入放在同一个事务中，任何异常整体回滚
    try:
        if not team1 or not team2:
            raise ValueError('该场比赛缺少参赛选手记录，无法累加胜负场次。')
        with db.transaction() as cur:
            affected = cur.execute(
                "UPDATE `match` SET set_score = %s, game_score = %s, actual_min = %s, "
                "status = 'F' WHERE match_id = %s",
                (set_score, game_score, actual_min, match_id),
            )
            if affected != 1:
                raise RuntimeError('更新比赛记录失败（影响 %d 行）。' % affected)
            for player_id in winners:
                cur.execute(
                    'UPDATE player SET win_count = win_count + 1 WHERE player_id = %s',
                    (player_id,))
            for player_id in losers:
                cur.execute(
                    'UPDATE player SET loss_count = loss_count + 1 WHERE player_id = %s',
                    (player_id,))
    except Exception as exc:  # noqa: BLE001 - 事务失败需要提示并回滚
        current_app.logger.exception('录入成绩失败，事务已回滚：%s', match_id)
        flash('成绩保存失败，事务已回滚，比赛状态与胜负场次均未改变：%s' % exc, 'danger')
        return redirect(url_for('results.index', match_id=match_id))

    auth.log_operation(
        '录入成绩', 'match:' + match_id,
        '盘分 %s 局分 %s 实际时长 %s 分钟' % (set_score, game_score,
                                         actual_min if actual_min is not None else '未填'))
    flash('比赛 %s 成绩录入成功：盘分 %s，局分 %s；比赛已结束，双方胜负场次已更新。'
          % (match_id, set_score, game_score), 'success')
    return redirect(url_for('results.index'))


# ---------------------------------------------------------------------------
# 统计报表（按项目 / 场地 / 性别 + 选手胜率榜）
# ---------------------------------------------------------------------------
def _build_stats():
    """汇总统计报表数据，供页面与 CSV 导出共用。"""
    # ---- 整体概览 ----
    overall = db.query_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'F' THEN 1 ELSE 0 END) AS finished, "
        "AVG(CASE WHEN status = 'F' THEN actual_min END) AS avg_min "
        "FROM `match`") or {}
    total_matches = int(overall.get('total') or 0)
    finished = int(overall.get('finished') or 0)
    avg_min = _avg_min(overall.get('avg_min'))
    total_wins = int(db.query_value(
        'SELECT COALESCE(SUM(win_count), 0) AS s FROM player', None, 0) or 0)
    total_losses = int(db.query_value(
        'SELECT COALESCE(SUM(loss_count), 0) AS s FROM player', None, 0) or 0)

    # ---- 按项目统计（场次 / 已结束 / 平均时长）----
    event_rows = db.query_all(
        "SELECT event_code, COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'F' THEN 1 ELSE 0 END) AS finished, "
        "AVG(CASE WHEN status = 'F' THEN actual_min END) AS avg_min "
        "FROM `match` GROUP BY event_code")
    event_map = {row['event_code']: row for row in event_rows}
    by_event = []
    for code, name in services.EVENT_NAMES.items():
        row = event_map.get(code) or {}
        by_event.append({
            'event_code': code,
            'event_name': name,
            'total': int(row.get('total') or 0),
            'finished': int(row.get('finished') or 0),
            'avg_min': _avg_min(row.get('avg_min')),
        })

    # ---- 按场地统计（使用场次 + 一队/二队胜负分布 + 平均时长）----
    court_rows = db.query_all(
        "SELECT court_id, COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'F' THEN 1 ELSE 0 END) AS finished, "
        "AVG(CASE WHEN status = 'F' THEN actual_min END) AS avg_min "
        "FROM `match` GROUP BY court_id")
    court_map = {row['court_id']: row for row in court_rows}
    win_map = {}   # court_id -> [一队胜, 二队胜]
    for row in db.query_all(
            "SELECT court_id, set_score FROM `match` "
            "WHERE status = 'F' AND set_score IS NOT NULL"):
        side = _score_winner_team(row['set_score'])
        if side:
            counter = win_map.setdefault(row['court_id'], [0, 0])
            counter[side - 1] += 1
    by_court = []
    for court in services.list_courts():
        row = court_map.get(court['court_id']) or {}
        sides = win_map.get(court['court_id'], [0, 0])
        by_court.append({
            'court_id': court['court_id'],
            'court_name': court['court_name'],
            'court_type': court.get('court_type') or '',
            'total': int(row.get('total') or 0),
            'finished': int(row.get('finished') or 0),
            'team1_wins': sides[0],
            'team2_wins': sides[1],
            'avg_min': _avg_min(row.get('avg_min')),
        })

    # ---- 按性别统计（男单/男双、女单/女双、混双三类）----
    gender_groups = (
        ('男子项目', ('MS', 'MD')),
        ('女子项目', ('WS', 'WD')),
        ('混合双打', ('XD',)),
    )
    finished_rows = db.query_all(
        "SELECT event_code, actual_min FROM `match` WHERE status = 'F'")
    by_gender = []
    for label, codes in gender_groups:
        rows = [r for r in finished_rows if r['event_code'] in codes]
        durations = [int(r['actual_min']) for r in rows if r['actual_min'] is not None]
        by_gender.append({
            'label': label,
            'finished': len(rows),
            'avg_min': round(sum(durations) / len(durations), 1) if durations else None,
            'share': round(len(rows) * 100.0 / finished, 1) if finished else 0.0,
        })

    # ---- 选手胜率榜（前 10，参赛场次为 0 的不显示）----
    top_players = []
    rank_rows = db.query_all(
        "SELECT player_id, name_cn, nation, gender, win_count, loss_count, "
        "(win_count + loss_count) AS played FROM player "
        "WHERE (win_count + loss_count) > 0 "
        "ORDER BY win_count / (win_count + loss_count) DESC, win_count DESC, "
        "rank_single = 0, rank_single, player_id LIMIT 10")
    for rank_no, row in enumerate(rank_rows, start=1):
        played = int(row['played'] or 0)
        row = dict(row)
        row['rank_no'] = rank_no
        row['win_rate'] = round(int(row['win_count']) * 100.0 / played, 1) if played else 0.0
        top_players.append(row)

    return {
        'total_matches': total_matches,
        'finished': finished,
        'avg_min': avg_min,
        'total_wins': total_wins,
        'total_losses': total_losses,
        'by_event': by_event,
        'by_court': by_court,
        'by_gender': by_gender,
        'top_players': top_players,
    }


@bp.route('/stats')
@auth.require_roles('admin', 'recorder')
def stats():
    """GET /results/stats：统计报表页。"""
    return render_template('results/stats.html', stats=_build_stats())


@bp.route('/stats/export')
@auth.require_roles('admin', 'recorder')
def export_stats():
    """GET /results/stats/export：导出统计报表 CSV（UTF-8 BOM）。"""
    stats = _build_stats()
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def row(values):
        """写一行，并对以 = + - @ 开头的单元格做公式注入防护（QAD-08）。"""
        writer.writerow([services.csv_safe(value) for value in values])

    row(['中国网球公开赛赛事运营管理系统 · 成绩统计报表'])
    row(['导出时间', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    row([])

    row(['一、整体概览'])
    row(['总比赛场次', '已结束场次', '平均实际时长(分钟)', '选手累计胜场', '选手累计负场'])
    row([stats['total_matches'], stats['finished'], _fmt(stats['avg_min']),
         stats['total_wins'], stats['total_losses']])
    row([])

    row(['二、按项目统计'])
    row(['项目', '总场次', '已结束场次', '平均实际时长(分钟)'])
    for item in stats['by_event']:
        row([item['event_name'], item['total'], item['finished'], _fmt(item['avg_min'])])
    row([])

    row(['三、按场地统计'])
    row(['场地编号', '场地名称', '场地类型', '总安排场次',
         '已结束场次', '一队胜', '二队胜', '平均实际时长(分钟)'])
    for item in stats['by_court']:
        row([item['court_id'], item['court_name'], item['court_type'],
             item['total'], item['finished'], item['team1_wins'],
             item['team2_wins'], _fmt(item['avg_min'])])
    row([])

    row(['四、按性别统计'])
    row(['组别', '已结束场次', '占比(%)', '平均实际时长(分钟)'])
    for item in stats['by_gender']:
        row([item['label'], item['finished'], item['share'], _fmt(item['avg_min'])])
    row([])

    row(['五、选手胜率榜（前 10）'])
    row(['名次', '运动员编号', '姓名', '国籍', '胜场', '负场', '胜率(%)'])
    for item in stats['top_players']:
        row([item['rank_no'], item['player_id'], item['name_cn'],
             item['nation'], item['win_count'], item['loss_count'], item['win_rate']])

    # utf-8-sig 的 BOM，保证 Excel 打开 CSV 中文不乱码
    payload = '\ufeff' + buffer.getvalue()
    filename = 'results_stats_%s.csv' % datetime.now().strftime('%Y%m%d_%H%M%S')
    return Response(
        payload.encode('utf-8'),
        content_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=%s' % filename},
    )
