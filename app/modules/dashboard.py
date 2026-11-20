# coding: utf-8
"""
工作台首页（报告 4.6 节表 11：当日比赛场次、待处理冲突数、待录比分场次三个提示条与快捷入口）。
"""
from flask import Blueprint, render_template

from .. import auth, db, services, validation

bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


@bp.route('/')
@auth.require_roles('admin', 'scheduler', 'recorder')
def index():
    staff = auth.current_staff()
    day = services.default_show_date()
    stats = services.dashboard_stats(day)
    stats['conflict_count'] = len(validation.scan_conflicts())

    today_matches = services.list_matches(date=day, limit=10)
    in_progress = services.list_matches(statuses=['P'], limit=5)
    court_summary = services.list_courts()
    recent_logs = db.query_all(
        'SELECT staff_name, action, target, detail, created_at FROM sys_log '
        'ORDER BY log_id DESC LIMIT 8')

    return render_template(
        'dashboard.html',
        staff=staff,
        stats=stats,
        show_date=day,
        today_matches=today_matches,
        in_progress=in_progress,
        court_summary=court_summary,
        recent_logs=recent_logs,
    )
