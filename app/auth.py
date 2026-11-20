# coding: utf-8
"""
系统管理与权限控制：登录、退出、修改密码、角色菜单、操作日志。

对应报告：
    2.2 节 用户角色与权限划分
    2.4 节 密码加盐哈希、连续 3 次错误锁定 10 分钟、会话超时 30 分钟
    4.7 节 "角色—菜单—操作" 三级权限控制
"""
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, services

bp = Blueprint('auth', __name__)

# ---------------------------------------------------------------------------
# 菜单定义：角色决定可见菜单（2.2 / 4.7 节）
# roles=None 表示登录后即可见；public 菜单未登录也可见
# ---------------------------------------------------------------------------
MENU = [
    {'label': '工作台', 'icon': 'bi-speedometer2', 'endpoint': 'dashboard.index',
     'roles': {'admin', 'scheduler', 'recorder'}},
    {'label': '基础数据管理', 'icon': 'bi-people', 'roles': {'admin', 'scheduler'},
     'children': [
         {'label': '运动员档案', 'icon': 'bi-person-badge', 'endpoint': 'base_data.player_list'},
         {'label': '裁判档案', 'icon': 'bi-star', 'endpoint': 'base_data.referee_list'},
         {'label': '场地档案', 'icon': 'bi-building', 'endpoint': 'base_data.court_list'},
     ]},
    {'label': '赛程编排管理', 'icon': 'bi-calendar3', 'roles': {'admin', 'scheduler'},
     'children': [
         {'label': '赛程编排', 'icon': 'bi-calendar-plus', 'endpoint': 'schedule.index'},
         {'label': '赛程表', 'icon': 'bi-table', 'endpoint': 'schedule.list_all'},
     ]},
    {'label': '成绩管理', 'icon': 'bi-trophy', 'roles': {'admin', 'recorder'},
     'children': [
         {'label': '成绩录入', 'icon': 'bi-pencil-square', 'endpoint': 'results.index'},
         {'label': '统计报表', 'icon': 'bi-bar-chart', 'endpoint': 'results.stats'},
     ]},
    {'label': '系统管理', 'icon': 'bi-gear', 'roles': {'admin'},
     'children': [
         {'label': '账号管理', 'icon': 'bi-person-gear', 'endpoint': 'system.staff_list'},
         {'label': '操作日志', 'icon': 'bi-journal-text', 'endpoint': 'system.log_list'},
     ]},
    {'label': '观众查询端', 'icon': 'bi-globe2', 'endpoint': 'public.index',
     'roles': {'admin', 'scheduler', 'recorder', 'public'}, 'target': '_blank'},
]

# 蓝图 -> 允许访问的角色（三级权限的"操作"一级在视图内再用 role_required 判断）
BLUEPRINT_ROLES = {
    'dashboard': {'admin', 'scheduler', 'recorder'},
    'system': {'admin'},
    'base_data': {'admin', 'scheduler'},
    'schedule': {'admin', 'scheduler'},
    'results': {'admin', 'recorder'},
}

# 未登录即可访问的端点（观众查询端 + 登录页）
PUBLIC_ENDPOINTS = {
    'auth.login', 'auth.logout', 'public.index', 'public.schedule',
    'public.players', 'public.player_detail', 'public.match_detail', 'static',
}


def safe_next_url(value, fallback=''):
    """校验登录后的跳转地址：只允许本站的相对路径。

    拒绝这三种情况，避免开放重定向与 HTTP 头注入（CRLF）：
        1. 外部地址（https://...、//evil.com、javascript:...）；
        2. 含控制字符（\\r、\\n、\\0）或反斜杠的地址；
        3. 不以单个 / 开头的地址。
    """
    value = (value or '').strip()
    if not value:
        return fallback
    if any(ch in value for ch in ('\r', '\n', '\x00', '\t', '\\')):
        return fallback
    if not value.startswith('/') or value.startswith('//'):
        return fallback
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return fallback
    return value


def visible_menu(role):
    """按角色过滤菜单（父级可见即子级全部可见）。"""
    items = []
    for item in MENU:
        if item.get('roles') and role not in item['roles']:
            continue
        node = {k: v for k, v in item.items() if k != 'children'}
        if item.get('children'):
            node['children'] = list(item['children'])
        items.append(node)
    return items


# ---------------------------------------------------------------------------
# 密码与登录
# ---------------------------------------------------------------------------
def hash_password(raw):
    return generate_password_hash(raw)


def verify_password(password_hash, raw):
    try:
        return check_password_hash(password_hash, raw)
    except (ValueError, TypeError):
        return False


def current_staff():
    """当前登录用户信息（来自会话）。"""
    staff_id = session.get('staff_id')
    if not staff_id:
        return None
    return {
        'staff_id': staff_id,
        'name': session.get('name', ''),
        'role': session.get('role', ''),
        'role_name': services.role_name(session.get('role', '')),
    }


def login_staff(staff_row):
    session.clear()
    session['staff_id'] = staff_row['staff_id']
    session['name'] = staff_row['name']
    session['role'] = staff_row['role']
    session['last_active'] = datetime.now().isoformat(timespec='seconds')


def require_roles(*roles):
    """视图级角色校验（防止绕过菜单直接输网址，对应 4.7 节）。"""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            staff = current_staff()
            if staff is None:
                flash('请先登录后再操作。', 'warning')
                return redirect(url_for('auth.login', next=request.path))
            if staff['role'] not in roles:
                return render_forbidden(staff)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def render_forbidden(staff=None):
    staff = staff or current_staff()
    return render_template('403.html', staff=staff), 403


def log_operation(action, target=None, detail=None):
    """写入操作日志（2.4 节：删除与状态变更留痕）。"""
    staff = current_staff()
    try:
        db.execute(
            'INSERT INTO sys_log (staff_id, staff_name, action, target, detail) '
            'VALUES (%s, %s, %s, %s, %s)',
            ((staff or {}).get('staff_id'), (staff or {}).get('name'),
             action, target, detail),
        )
    except Exception:  # 日志失败不影响主业务
        current_app.logger.exception('写操作日志失败')


# ---------------------------------------------------------------------------
# 登录 / 退出 / 修改密码
# ---------------------------------------------------------------------------
@bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('staff_id'):
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        login_name = (request.form.get('login_name') or '').strip()
        password = request.form.get('password') or ''
        # next 只接受本站相对路径，非法值直接忽略（QAD-01 / QAD-03）
        referer_next = safe_next_url(
            request.args.get('next') or request.form.get('next') or '')

        if not login_name or not password:
            flash('账号和密码不能为空。', 'danger')
            return render_template('login.html', login_name=login_name, next=referer_next), 200

        row = db.query_one('SELECT * FROM staff WHERE login_name = %s', (login_name,))
        now = datetime.now()

        if row is None or not verify_password(row['password_hash'], password):
            if row is not None:
                fails = int(row.get('fail_count') or 0) + 1
                locked_until = row.get('locked_until')
                if fails >= current_app.config['LOGIN_MAX_FAIL']:
                    locked_until = now + timedelta(minutes=current_app.config['LOGIN_LOCK_MIN'])
                    fails = 0
                    db.execute('UPDATE staff SET fail_count = %s, locked_until = %s WHERE staff_id = %s',
                               (fails, locked_until, row['staff_id']))
                    flash('连续 %d 次密码错误，账号已锁定 %d 分钟。'
                          % (current_app.config['LOGIN_MAX_FAIL'], current_app.config['LOGIN_LOCK_MIN']),
                          'danger')
                else:
                    db.execute('UPDATE staff SET fail_count = %s WHERE staff_id = %s',
                               (fails, row['staff_id']))
                    # 提示语与"账号不存在"保持一致，避免暴露账号是否存在（QAD-09）
                    flash('账号或密码错误。', 'danger')
            else:
                flash('账号或密码错误。', 'danger')
            return render_template('login.html', login_name=login_name, next=referer_next), 200

        if row['status'] != 'A':
            flash('该账号已停用，请联系系统管理员。', 'danger')
            return render_template('login.html', login_name=login_name, next=referer_next), 200

        locked_until = row.get('locked_until')
        if locked_until and locked_until > now:
            minutes = int((locked_until - now).total_seconds() // 60) + 1
            flash('账号已锁定，请 %d 分钟后再试。' % minutes, 'warning')
            return render_template('login.html', login_name=login_name, next=referer_next), 200

        # 登录成功：清零失败次数、记录登录日志
        db.execute('UPDATE staff SET fail_count = 0, locked_until = NULL WHERE staff_id = %s',
                   (row['staff_id'],))
        login_staff(row)
        db.execute('INSERT INTO sys_log (staff_id, staff_name, action, target, detail) '
                   'VALUES (%s, %s, %s, %s, %s)',
                   (row['staff_id'], row['name'], '登录', 'staff:' + row['staff_id'], '登录成功'))
        flash('欢迎回来，%s（%s）。' % (row['name'], services.role_name(row['role'])), 'success')
        return redirect(referer_next or url_for('dashboard.index'))

    return render_template('login.html', login_name='', next=request.args.get('next', ''))


@bp.route('/logout')
def logout():
    staff = current_staff()
    if staff:
        log_operation('退出', 'staff:' + staff['staff_id'], '退出登录')
    session.clear()
    flash('已安全退出登录。', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/password', methods=['GET', 'POST'])
def change_password():
    staff = current_staff()
    if staff is None:
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        old = request.form.get('old_password') or ''
        new = request.form.get('new_password') or ''
        confirm = request.form.get('confirm_password') or ''
        row = db.query_one('SELECT * FROM staff WHERE staff_id = %s', (staff['staff_id'],))
        if not verify_password(row['password_hash'], old):
            flash('原密码不正确。', 'danger')
        elif len(new) < 6:
            flash('新密码长度不能少于 6 位。', 'danger')
        elif new != confirm:
            flash('两次输入的新密码不一致。', 'danger')
        else:
            db.execute('UPDATE staff SET password_hash = %s WHERE staff_id = %s',
                       (hash_password(new), staff['staff_id']))
            log_operation('修改密码', 'staff:' + staff['staff_id'], '修改本人登录密码')
            # 先清会话再 flash，否则提示会被 session.clear() 一起清掉（QAD-04）
            session.clear()
            flash('密码修改成功，请使用新密码重新登录。', 'success')
            return redirect(url_for('auth.login'))
    return render_template('password.html', staff=staff)
