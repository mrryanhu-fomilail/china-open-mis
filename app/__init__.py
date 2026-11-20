# coding: utf-8
"""
Flask 应用工厂：注册蓝图、会话守卫、模板全局变量、错误处理。

对应报告：
    4.1 节 三层 B/S 架构（表示层 = templates，业务逻辑层 = modules，数据层 = db + MySQL）
    4.7 节 三级权限控制：角色决定菜单，蓝图决定页面，视图内再校验写操作
"""
from datetime import datetime

import pymysql
from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.routing import BuildError

from . import auth, db, services


def _same_origin():
    """写操作的同源校验：有 Origin/Referer 时必须与本站一致（轻量 CSRF 防护，QAD-10）。

    curl、测试客户端等不发这两个头的请求放行，真实浏览器跨站表单会被拦下。
    """
    host = request.host
    origin = request.headers.get('Origin')
    if origin:
        return origin in ('http://%s' % host, 'https://%s' % host)
    referer = request.headers.get('Referer')
    if referer:
        return referer.startswith('http://%s/' % host) or referer.startswith('https://%s/' % host)
    return True


def safe_url(endpoint, **values):
    """url_for 的安全版本：端点尚未实现时返回 #，避免整页渲染失败。"""
    try:
        return url_for(endpoint, **values)
    except BuildError:
        return '#'


def create_app(config_object='config'):
    app = Flask(__name__)
    app.config.from_object(config_object)

    # ------------------------------------------------------------------
    # 数据层：请求结束时提交/回滚并关闭连接
    # ------------------------------------------------------------------
    app.teardown_appcontext(db.close_connection)

    # ------------------------------------------------------------------
    # 蓝图注册（五个业务模块 + 工作台 + 登录鉴权）
    # ------------------------------------------------------------------
    from .modules import base_data, dashboard, public, results, schedule, system

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(system.bp, url_prefix='/system')
    app.register_blueprint(base_data.bp, url_prefix='/base')
    app.register_blueprint(schedule.bp, url_prefix='/schedule')
    app.register_blueprint(results.bp, url_prefix='/results')
    app.register_blueprint(public.bp, url_prefix='/public')

    @app.route('/')
    def index():
        """根路径进入观众查询端（免登录），页面上提供工作人员登录入口。"""
        return redirect(url_for('public.index'))

    # ------------------------------------------------------------------
    # 会话守卫：登录校验、超时控制、蓝图级角色校验（TC-08）
    # ------------------------------------------------------------------
    @app.before_request
    def guard():
        endpoint = request.endpoint or ''
        if endpoint in ('static', 'index') or request.path.startswith('/static'):
            return None

        # 写操作做同源校验，防止跨站伪造请求（QAD-10）
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and not _same_origin():
            app.logger.warning('拦截跨站写请求：%s %s origin=%s',
                               request.method, request.path,
                               request.headers.get('Origin'))
            abort(403)

        blueprint = request.blueprint
        # 观众端与登录页免登录访问
        if blueprint in ('public', 'auth'):
            session['last_active'] = datetime.now().isoformat(timespec='seconds')
            g.staff = auth.current_staff()
            return None

        if not session.get('staff_id'):
            flash('请先登录后再访问该功能。', 'warning')
            return redirect(url_for('auth.login', next=request.path))

        # 会话超时（2.4 节：30 分钟无操作重新登录）
        timeout_min = app.config['SESSION_TIMEOUT_MIN']
        last_active = session.get('last_active')
        last_dt = None
        if last_active:
            try:
                last_dt = datetime.fromisoformat(last_active)
            except ValueError:
                last_dt = None
        if last_dt and (datetime.now() - last_dt).total_seconds() > timeout_min * 60:
            session.clear()
            flash('会话已超时（%d 分钟无操作），请重新登录。' % timeout_min, 'warning')
            return redirect(url_for('auth.login', next=request.path))

        session['last_active'] = datetime.now().isoformat(timespec='seconds')

        # 每次请求都从数据库核对账号状态与角色，降权/停用立即生效（QAD-02）
        staff_row = db.query_one(
            'SELECT staff_id, name, role, status FROM staff WHERE staff_id = %s',
            (session.get('staff_id'),))
        if staff_row is None or staff_row['status'] != 'A':
            session.clear()
            flash('账号已停用或被删除，请重新登录。', 'warning')
            return redirect(url_for('auth.login'))
        if staff_row['role'] != session.get('role'):
            session.clear()
            flash('账号角色已被管理员调整，请重新登录后继续操作。', 'warning')
            return redirect(url_for('auth.login'))

        g.staff = auth.current_staff()

        # 蓝图级权限：例如成绩录入员直接访问 /schedule 会被拦截
        allowed_roles = auth.BLUEPRINT_ROLES.get(blueprint)
        if allowed_roles and g.staff and g.staff['role'] not in allowed_roles:
            return auth.render_forbidden(g.staff)
        return None

    # ------------------------------------------------------------------
    # 模板全局变量与函数
    # ------------------------------------------------------------------
    @app.context_processor
    def inject_globals():
        staff = getattr(g, 'staff', None) or auth.current_staff()
        role = staff['role'] if staff else None
        menus = auth.visible_menu(role) if role else []
        return {
            'current_staff': staff,
            'menus': menus,
            'EVENT_NAMES': services.EVENT_NAMES,
            'ROUND_NAMES': services.ROUND_NAMES,
            'MATCH_STATUS': services.MATCH_STATUS,
            'COURT_STATUS': services.COURT_STATUS,
            'DUTY_STATUS': services.DUTY_STATUS,
            'STAFF_ROLES': services.STAFF_ROLES,
            'STAFF_STATUS': services.STAFF_STATUS,
            'MATCH_BADGE': {'N': 'secondary', 'P': 'warning', 'F': 'success'},
            'COURT_BADGE': {'A': 'success', 'M': 'danger'},
            'DUTY_BADGE': {'A': 'success', 'B': 'info', 'C': 'secondary'},
            'event_name': services.event_name,
            'round_name': services.round_name,
            'status_name': services.status_name,
            'court_status_name': services.court_status_name,
            'duty_status_name': services.duty_status_name,
            'role_name': services.role_name,
            'end_time': services.end_time,
            'today': services.today_str,
            'event_start': app.config['EVENT_START'],
            'event_end': app.config['EVENT_END'],
            'app_name': '中国网球公开赛赛事运营管理系统',
            'app_short': '中网赛事运营管理系统',
            'safe_url': safe_url,
        }

    # ------------------------------------------------------------------
    # 错误处理（4.7 节：系统类异常统一捕获并回滚）
    # ------------------------------------------------------------------
    @app.errorhandler(403)
    def forbidden(err):
        staff = getattr(g, 'staff', None) or auth.current_staff()
        return render_template('403.html', staff=staff), 403

    @app.errorhandler(404)
    def not_found(err):
        return render_template('404.html', staff=getattr(g, 'staff', None)), 404

    @app.errorhandler(pymysql.err.OperationalError)
    def db_error(err):
        app.logger.exception('数据库连接失败')
        return render_template('db_error.html', error=str(err),
                               cfg=app.config['DB_CONFIG']), 500

    @app.errorhandler(500)
    def server_error(err):
        app.logger.exception('系统异常')
        # 只在调试模式下回显异常细节，正式演示时给固定文案（避免泄露内部信息）
        detail = str(err) if app.config.get('DEBUG') else ''
        return render_template('500.html', staff=getattr(g, 'staff', None),
                               error=detail), 500

    return app
