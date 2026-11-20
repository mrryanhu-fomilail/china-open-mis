# coding: utf-8
"""
模块1 系统管理：工作人员账号管理与操作日志查询。

对应报告：
    2.2 节 用户角色与权限划分
    2.4 节 账号状态、密码加盐哈希、删除与状态变更留痕
    4.7 节 "角色—菜单—操作" 三级权限控制

全部页面与写操作仅系统管理员（admin）可用：
    GET  /system/staff                     账号列表 + 关键字/角色/状态检索
    POST /system/staff/create              新增账号（员工编号自动生成）
    POST /system/staff/<staff_id>/update   修改姓名/岗位/角色
    POST /system/staff/<staff_id>/reset    重置密码为默认密码 123456
    POST /system/staff/<staff_id>/toggle   启用/停用账号
    GET  /system/logs                      操作日志（按类型/关键字筛选，分页，最多 200 条）

安全要点：
    1. 所有 SQL 均使用 %s 参数占位，不做字符串拼接；
    2. 密码只保存 werkzeug 加盐哈希，重置后明文仅提示一次；
    3. 不允许停用当前登录账号，也不允许停用/降级最后一个启用状态的管理员；
    4. 新增、修改、重置密码、启停用均写入 sys_log 操作日志。
"""
import re

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

from .. import auth, db, services

bp = Blueprint('system', __name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_PASSWORD = '123456'          # 重置后的初始密码（2.4 节：库里只存哈希）
MIN_PASSWORD_LEN = 6                 # 与 auth.change_password 保持一致
MAX_PASSWORD_LEN = 50
TEXT_MAX_LEN = 30                    # 姓名/岗位/登录账号在表中均为 VARCHAR(30)
LOG_PAGE_SIZE = 20                   # 操作日志每页条数
LOG_MAX_ROWS = 200                   # 操作日志最多显示 200 条
LOGIN_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+$')

ROLE_CODES = tuple(services.STAFF_ROLES.keys())
STATUS_CODES = tuple(services.STAFF_STATUS.keys())

# 常用操作类型（用于日志筛选下拉框；库里若有其它历史值会合并显示）
COMMON_LOG_ACTIONS = ['登录', '退出', '新增', '修改', '删除', '状态变更',
                      '重置密码', '修改密码', '录入成绩']

# 操作类型 -> Bootstrap 徽章颜色
LOG_ACTION_BADGE = {
    '登录': 'success', '退出': 'secondary',
    '新增': 'primary', '修改': 'info', '删除': 'danger',
    '状态变更': 'warning', '重置密码': 'warning', '修改密码': 'info',
    '录入成绩': 'dark',
}


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _clean(value):
    """去除表单/查询字符串首尾空白，None 统一转为空串。"""
    return (value or '').strip()


def _load_staff(staff_id):
    """按员工编号读取账号记录，不存在时返回 None。"""
    return db.query_one('SELECT * FROM staff WHERE staff_id = %s', (staff_id,))


def _next_staff_id():
    """生成员工编号：S + 4 位流水号（报告 4.5 节 STAFF 表主键）。"""
    return services.next_serial('staff', 'staff_id', 'S', 4)


def _active_admin_count(exclude_staff_id=None):
    """统计启用状态的系统管理员数量（防止系统失去最后一名管理员）。"""
    sql = "SELECT COUNT(*) AS c FROM staff WHERE role = 'admin' AND status = 'A'"
    params = []
    if exclude_staff_id:
        sql += ' AND staff_id <> %s'
        params.append(exclude_staff_id)
    return int(db.query_value(sql, tuple(params), 0) or 0)


def _validate_profile(form):
    """校验姓名/岗位/角色（新增与修改共用），返回 (data, errors)。"""
    data = {
        'name': _clean(form.get('name')),
        'post': _clean(form.get('post')),
        'role': _clean(form.get('role')),
    }
    errors = {}

    if not data['name']:
        errors['name'] = '姓名不能为空。'
    elif len(data['name']) > TEXT_MAX_LEN:
        errors['name'] = '姓名不能超过 %d 个字符。' % TEXT_MAX_LEN

    if not data['post']:
        errors['post'] = '岗位不能为空。'
    elif len(data['post']) > TEXT_MAX_LEN:
        errors['post'] = '岗位不能超过 %d 个字符。' % TEXT_MAX_LEN

    if not data['role']:
        errors['role'] = '请选择角色。'
    elif data['role'] not in ROLE_CODES:
        errors['role'] = '角色不合法，请从下拉列表中选择。'

    return data, errors


def _validate_login_name(login_name):
    """校验登录账号：非空、长度、字符集、唯一性；返回错误说明或 None。"""
    if not login_name:
        return '登录账号不能为空。'
    if len(login_name) > TEXT_MAX_LEN:
        return '登录账号不能超过 %d 个字符。' % TEXT_MAX_LEN
    if not LOGIN_NAME_PATTERN.match(login_name):
        return '登录账号只能包含字母、数字、下划线、点和短横线。'
    row = db.query_one('SELECT staff_id FROM staff WHERE login_name = %s', (login_name,))
    if row:
        return '登录账号 %s 已被占用，请换一个。' % login_name
    return None


def _flash_errors(prefix, errors):
    """把校验错误合并成一条中文 danger 提示。"""
    flash('%s：%s' % (prefix, '；'.join(errors.values())), 'danger')


# ---------------------------------------------------------------------------
# 账号管理
# ---------------------------------------------------------------------------
@bp.route('/staff')
@auth.require_roles('admin')
def staff_list():
    """账号列表：支持按关键字（编号/姓名/账号/岗位）、角色、状态检索。"""
    keyword = _clean(request.args.get('keyword'))
    role = _clean(request.args.get('role'))
    status = _clean(request.args.get('status'))

    where, params = [], []
    if keyword:
        like = '%' + keyword + '%'
        where.append('(staff_id LIKE %s OR name LIKE %s OR login_name LIKE %s OR post LIKE %s)')
        params.extend([like, like, like, like])
    if role in ROLE_CODES:
        where.append('role = %s')
        params.append(role)
    if status in STATUS_CODES:
        where.append('status = %s')
        params.append(status)

    sql = 'SELECT * FROM staff'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    # 启用账号排在停用账号前面，同状态按员工编号排序
    sql += " ORDER BY status = 'D', staff_id"
    staff_rows = db.query_all(sql, tuple(params))

    stats = {
        'total': int(db.query_value('SELECT COUNT(*) AS c FROM staff', None, 0) or 0),
        'active': int(db.query_value("SELECT COUNT(*) AS c FROM staff WHERE status = 'A'",
                                     None, 0) or 0),
        'disabled': int(db.query_value("SELECT COUNT(*) AS c FROM staff WHERE status = 'D'",
                                       None, 0) or 0),
        'admins': int(db.query_value("SELECT COUNT(*) AS c FROM staff WHERE role = 'admin'",
                                     None, 0) or 0),
    }

    return render_template(
        'system/staff.html',
        staff_rows=staff_rows,
        stats=stats,
        keyword=keyword,
        role=role,
        status=status,
        role_codes=ROLE_CODES,
        status_codes=STATUS_CODES,
        current=auth.current_staff(),
        default_password=DEFAULT_PASSWORD,
    )


@bp.route('/staff/create', methods=['POST'])
@auth.require_roles('admin')
def staff_create():
    """新增工作人员账号：员工编号自动生成，初始状态为启用。"""
    data, errors = _validate_profile(request.form)

    login_name = _clean(request.form.get('login_name'))
    login_error = _validate_login_name(login_name)
    if login_error:
        errors['login_name'] = login_error

    password = request.form.get('password') or ''
    if not password:
        errors['password'] = '初始密码不能为空。'
    elif len(password) < MIN_PASSWORD_LEN:
        errors['password'] = '初始密码长度不能少于 %d 位。' % MIN_PASSWORD_LEN
    elif len(password) > MAX_PASSWORD_LEN:
        errors['password'] = '初始密码长度不能超过 %d 位。' % MAX_PASSWORD_LEN

    if errors:
        _flash_errors('新增账号失败', errors)
        return redirect(url_for('system.staff_list'))

    staff_id = _next_staff_id()
    try:
        db.execute(
            'INSERT INTO staff (staff_id, name, post, login_name, password_hash, role, status) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (staff_id, data['name'], data['post'], login_name,
             auth.hash_password(password), data['role'], 'A'),
        )
    except Exception:  # 并发下编号/账号冲突等，避免 500
        current_app.logger.exception('新增工作人员账号失败')
        flash('新增账号失败：员工编号或登录账号发生冲突，请刷新页面后重试。', 'danger')
        return redirect(url_for('system.staff_list'))

    auth.log_operation(
        '新增', 'staff:' + staff_id,
        '新增账号 %s（%s，%s）' % (login_name, data['name'], services.role_name(data['role'])),
    )
    flash('新增账号成功：%s（%s），初始密码已设置。'
          % (data['name'], services.role_name(data['role'])), 'success')
    return redirect(url_for('system.staff_list'))


@bp.route('/staff/<staff_id>/update', methods=['POST'])
@auth.require_roles('admin')
def staff_update(staff_id):
    """修改账号资料：姓名、岗位、角色（登录账号与密码不在此修改）。"""
    staff = _load_staff(staff_id)
    if staff is None:
        flash('该工作人员账号不存在或已被删除。', 'danger')
        return redirect(url_for('system.staff_list'))

    data, errors = _validate_profile(request.form)
    current = auth.current_staff()

    # 防止管理员把自己改成别的角色后失去系统管理入口
    if current and staff_id == current['staff_id'] and data['role'] != 'admin':
        errors['role'] = '不能修改当前登录账号的角色。'
    # 系统必须保留至少一名启用状态的管理员
    if (not errors and staff['role'] == 'admin' and data['role'] != 'admin'
            and _active_admin_count(exclude_staff_id=staff_id) == 0):
        errors['role'] = '系统至少需要保留一名启用状态的系统管理员，不能取消最后一个管理员。'

    if errors:
        _flash_errors('修改账号失败', errors)
        return redirect(url_for('system.staff_list'))

    changes = []
    if staff['name'] != data['name']:
        changes.append('姓名 %s -> %s' % (staff['name'], data['name']))
    if staff['post'] != data['post']:
        changes.append('岗位 %s -> %s' % (staff['post'], data['post']))
    if staff['role'] != data['role']:
        changes.append('角色 %s -> %s'
                       % (services.role_name(staff['role']), services.role_name(data['role'])))

    if not changes:
        flash('账号资料未发生变化。', 'info')
        return redirect(url_for('system.staff_list'))

    db.execute(
        'UPDATE staff SET name = %s, post = %s, role = %s WHERE staff_id = %s',
        (data['name'], data['post'], data['role'], staff_id),
    )
    # 修改的是本人资料时同步会话中的姓名，避免顶栏显示旧名字
    if current and staff_id == current['staff_id']:
        session['name'] = data['name']

    auth.log_operation('修改', 'staff:' + staff_id, '修改工作人员资料：' + '；'.join(changes))
    flash('账号资料已保存：%s（%s）。' % (data['name'], services.role_name(data['role'])), 'success')
    return redirect(url_for('system.staff_list'))


@bp.route('/staff/<staff_id>/reset', methods=['POST'])
@auth.require_roles('admin')
def staff_reset(staff_id):
    """把指定账号的密码重置为默认密码 123456，并解除登录锁定。"""
    staff = _load_staff(staff_id)
    if staff is None:
        flash('该工作人员账号不存在或已被删除。', 'danger')
        return redirect(url_for('system.staff_list'))

    db.execute(
        'UPDATE staff SET password_hash = %s, fail_count = 0, locked_until = NULL '
        'WHERE staff_id = %s',
        (auth.hash_password(DEFAULT_PASSWORD), staff_id),
    )
    auth.log_operation(
        '重置密码', 'staff:' + staff_id,
        '重置 %s（%s）的登录密码为初始密码' % (staff['name'], staff['login_name']),
    )
    flash('已将 %s 的登录密码重置为 %s，请提醒本人登录后修改密码。'
          % (staff['name'], DEFAULT_PASSWORD), 'success')
    return redirect(url_for('system.staff_list'))


@bp.route('/staff/<staff_id>/toggle', methods=['POST'])
@auth.require_roles('admin')
def staff_toggle(staff_id):
    """启用 / 停用账号（停用后该账号无法登录，2.2 节账号状态管理）。"""
    staff = _load_staff(staff_id)
    if staff is None:
        flash('该工作人员账号不存在或已被删除。', 'danger')
        return redirect(url_for('system.staff_list'))

    current = auth.current_staff()
    if current and staff_id == current['staff_id']:
        flash('不能停用当前登录的账号，请使用其他管理员账号操作。', 'danger')
        return redirect(url_for('system.staff_list'))

    new_status = 'D' if staff['status'] == 'A' else 'A'
    if (new_status == 'D' and staff['role'] == 'admin'
            and _active_admin_count(exclude_staff_id=staff_id) == 0):
        flash('系统至少需要保留一名启用状态的系统管理员，不能停用该账号。', 'danger')
        return redirect(url_for('system.staff_list'))

    db.execute(
        'UPDATE staff SET status = %s, fail_count = 0, locked_until = NULL WHERE staff_id = %s',
        (new_status, staff_id),
    )
    old_status_name = services.STAFF_STATUS.get(staff['status'], staff['status'])
    new_status_name = services.STAFF_STATUS.get(new_status, new_status)
    auth.log_operation(
        '状态变更', 'staff:' + staff_id,
        '账号状态 %s -> %s（%s）' % (old_status_name, new_status_name, staff['login_name']),
    )
    flash('账号 %s（%s）已%s。'
          % (staff['name'], staff['login_name'], '停用' if new_status == 'D' else '启用'),
          'success')
    return redirect(url_for('system.staff_list'))


# ---------------------------------------------------------------------------
# 操作日志
# ---------------------------------------------------------------------------
@bp.route('/logs')
@auth.require_roles('admin')
def log_list():
    """操作日志列表：按操作类型、关键字筛选，分页显示最近 200 条。"""
    action = _clean(request.args.get('action'))
    keyword = _clean(request.args.get('keyword'))
    page = request.args.get('page', type=int) or 1
    if page < 1:
        page = 1

    where, params = [], []
    if action:
        where.append('action = %s')
        params.append(action)
    if keyword:
        like = '%' + keyword + '%'
        where.append('(staff_name LIKE %s OR staff_id LIKE %s OR target LIKE %s OR detail LIKE %s)')
        params.extend([like, like, like, like])
    where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''

    total_count = int(db.query_value(
        'SELECT COUNT(*) AS c FROM sys_log' + where_sql, tuple(params), 0) or 0)
    # 最多只展示最近 200 条（LOG_MAX_ROWS），分页在这 200 条之内进行
    visible_total = min(total_count, LOG_MAX_ROWS)
    pages = max(1, (visible_total + LOG_PAGE_SIZE - 1) // LOG_PAGE_SIZE)
    if page > pages:
        page = pages
    offset = (page - 1) * LOG_PAGE_SIZE

    logs = db.query_all(
        'SELECT log_id, staff_id, staff_name, action, target, detail, created_at '
        'FROM sys_log' + where_sql + ' ORDER BY log_id DESC LIMIT %s OFFSET %s',
        tuple(params) + (LOG_PAGE_SIZE, offset),
    )

    def _page_url(page_no):
        args = {'page': page_no}
        if action:
            args['action'] = action
        if keyword:
            args['keyword'] = keyword
        return url_for('system.log_list', **args)

    page_links = [{'no': no, 'url': _page_url(no)} for no in range(1, pages + 1)]

    # 操作类型下拉框：常用类型在前，数据库中其它历史类型追加在后面
    existing = {row['action'] for row in db.query_all('SELECT DISTINCT action FROM sys_log')}
    action_options = ([a for a in COMMON_LOG_ACTIONS if a in existing]
                      + sorted(existing - set(COMMON_LOG_ACTIONS)))

    stats = {
        'total': int(db.query_value('SELECT COUNT(*) AS c FROM sys_log', None, 0) or 0),
        'today': int(db.query_value(
            'SELECT COUNT(*) AS c FROM sys_log WHERE created_at >= CURDATE()', None, 0) or 0),
        'operators': int(db.query_value(
            'SELECT COUNT(DISTINCT staff_id) AS c FROM sys_log WHERE staff_id IS NOT NULL',
            None, 0) or 0),
    }

    return render_template(
        'system/logs.html',
        logs=logs,
        stats=stats,
        total_count=total_count,
        page=page,
        pages=pages,
        page_links=page_links,
        action=action,
        keyword=keyword,
        action_options=action_options,
        action_badges=LOG_ACTION_BADGE,
        log_max_rows=LOG_MAX_ROWS,
        log_page_size=LOG_PAGE_SIZE,
    )
