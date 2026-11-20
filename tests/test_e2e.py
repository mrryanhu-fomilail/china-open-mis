# coding: utf-8
"""
端到端功能验收脚本（对应课程设计报告 5.3 节测试用例 TC-01 ~ TC-08）

运行方式：
    cd china_open_mis
    python tests/test_e2e.py

脚本使用 Flask 测试客户端直接驱动整个应用（不占用端口），连接 .env 中配置的 MySQL。
每个用例独立打印 PASS / FAIL，最后给出汇总；有失败时退出码非 0。
脚本会自己创建测试数据并在结束时清理（不改动演示数据）。
"""
import os
import sys
import traceback
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app, db  # noqa: E402
from app import services, validation  # noqa: E402

RESULTS = []
CREATED = {'players': [], 'matches': [], 'staff': []}
# 录分用例会真实修改一场演示比赛，这里记录原始值，测试结束时还原
RESTORE = {'matches': [], 'players': []}


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print('  [%s] %s%s' % ('PASS' if condition else 'FAIL', name,
                           ('  -> ' + str(detail)) if detail and not condition else ''))
    return bool(condition)


def login(client, name='admin01', password='123456'):
    return client.post('/login', data={'login_name': name, 'password': password},
                       follow_redirects=True)


def new_client():
    return app.test_client()


def cleanup():
    """清理本次测试创建的数据，并还原被录分用例修改的演示数据。"""
    try:
        with app.app_context():
            for mid in CREATED['matches']:
                db.execute('DELETE FROM `match` WHERE match_id = %s', (mid,))
            for pid in CREATED['players']:
                db.query_one('SELECT player_id FROM player WHERE player_id = %s', (pid,)) and \
                    db.execute('DELETE FROM player WHERE player_id = %s', (pid,))
            for sid in CREATED['staff']:
                db.execute('DELETE FROM staff WHERE staff_id = %s', (sid,))
            # 还原创分用例改动的比赛与选手胜负场次，避免演示数据被测试污染
            for mid, snap in RESTORE['matches']:
                db.execute(
                    'UPDATE `match` SET status = %s, set_score = %s, game_score = %s, '
                    'actual_min = %s WHERE match_id = %s',
                    (snap['status'], snap['set_score'], snap['game_score'],
                     snap['actual_min'], mid))
            for pid, wins, losses in RESTORE['players']:
                db.execute('UPDATE player SET win_count = %s, loss_count = %s '
                           'WHERE player_id = %s', (wins, losses, pid))
    except Exception:
        traceback.print_exc()


def api_match_exists(match_id):
    with app.app_context():
        return db.query_one('SELECT match_id FROM `match` WHERE match_id = %s', (match_id,)) is not None


def case_public_pages():
    print('\n[1] 公开查询端（模块5，免登录只读）')
    client = new_client()
    r = client.get('/public/')
    check('GET /public/ 返回 200', r.status_code == 200, r.status_code)
    check('公开首页包含赛程信息', '赛程' in r.data.decode())
    r = client.get('/public/players')
    check('GET /public/players 返回 200', r.status_code == 200, r.status_code)
    # 取一个选手看详情
    with app.app_context():
        row = db.query_one('SELECT player_id FROM player ORDER BY player_id LIMIT 1')
    if row:
        r = client.get('/public/player/%s' % row['player_id'])
        check('GET /public/player/<id> 返回 200', r.status_code == 200, r.status_code)
    r = client.get('/dashboard/')
    check('未登录访问 /dashboard/ 被重定向到登录页', r.status_code == 302, r.status_code)


def case_login_and_permission():
    print('\n[2] 登录与三级权限（FR-01 / TC-08）')
    client = new_client()
    r = client.post('/login', data={'login_name': 'admin01', 'password': 'wrong'},
                    follow_redirects=True)
    check('错误密码被拒绝并给出提示', r.status_code == 200 and ('错误' in r.data.decode()))

    client = new_client()
    login(client)
    r = client.get('/dashboard/')
    check('管理员登录后工作台可访问', r.status_code == 200, r.status_code)
    check('工作台中显示角色菜单', '赛程编排' in r.data.decode())

    # 成绩录入员直接访问赛程编排页 -> 403（TC-08）
    recorder = new_client()
    login(recorder, 'record01', '123456')
    r = recorder.get('/schedule/')
    check('成绩录入员访问 /schedule/ 返回 403（TC-08）', r.status_code == 403, r.status_code)
    check('403 页面提示无权访问', '无权' in r.data.decode())

    # 编排员访问成绩录入 -> 403
    scheduler = new_client()
    login(scheduler, 'schedule01', '123456')
    r = scheduler.get('/results/')
    check('编排员访问 /results/ 返回 403', r.status_code == 403, r.status_code)


def case_base_data():
    print('\n[3] 基础数据管理（FR-03 ~ FR-06）')
    scheduler = new_client()
    login(scheduler, 'schedule01', '123456')

    r = scheduler.get('/base/player')
    check('运动员档案页可访问', r.status_code == 200, r.status_code)
    r = scheduler.get('/base/referee')
    check('裁判档案页可访问', r.status_code == 200, r.status_code)
    r = scheduler.get('/base/court')
    check('场地档案页可访问', r.status_code == 200, r.status_code)

    # 新增运动员（编号由页面生成，这里直接按接口约定提交）
    with app.app_context():
        pid = services.next_serial('player', 'player_id', 'P', 7)
    form = {
        'player_id': '', 'name_cn': '测试选手甲', 'name_en': 'Test Player A',
        'nation': 'CHN', 'gender': 'M', 'rank_single': '199', 'rank_double': '0',
        'association': '测试协会', 'wildcard': 'Y',
    }
    r = scheduler.post('/base/player/save', data=form, follow_redirects=True)
    with app.app_context():
        saved = db.query_one('SELECT * FROM player WHERE player_id = %s', (pid,))
    if saved:
        CREATED['players'].append(pid)
    check('新增运动员成功写库（FR-03）', saved is not None, pid)
    if saved:
        check('新增运动员字段正确', saved['name_cn'] == '测试选手甲' and saved['nation'] == 'CHN')
        # 编辑
        form['player_id'] = pid
        form['rank_single'] = '188'
        scheduler.post('/base/player/save', data=form, follow_redirects=True)
        with app.app_context():
            edited = db.query_one('SELECT rank_single FROM player WHERE player_id = %s', (pid,))
        check('修改运动员成功', edited and int(edited['rank_single']) == 188)
        # 检索（FR-04）
        r = scheduler.get('/base/player?keyword=测试选手甲')
        check('按关键字检索到新增运动员（FR-04）', '测试选手甲' in r.data.decode())
        # 删除
        scheduler.post('/base/player/%s/delete' % pid, follow_redirects=True)
        with app.app_context():
            gone = db.query_one('SELECT player_id FROM player WHERE player_id = %s', (pid,))
        check('删除运动员成功', gone is None)
        if gone is None:
            CREATED['players'].remove(pid)

    # 场地维护状态：改为维护中的场地不能出现在编排候选（FR-06 / TC-03）
    with app.app_context():
        court = db.query_one("SELECT court_id, status FROM court WHERE status = 'A' ORDER BY court_id DESC LIMIT 1")
    if court:
        cid = court['court_id']
        scheduler.post('/base/court/%s/status' % cid, data={'status': 'M'}, follow_redirects=True)
        with app.app_context():
            after = db.query_one('SELECT status FROM court WHERE court_id = %s', (cid,))
            available = [c['court_id'] for c in services.list_courts(available_only=True)]
        check('场地状态改为维护中（FR-06）', after and after['status'] == 'M')
        check('维护中的场地不出现在可用候选列表（TC-03）', cid not in available)
        scheduler.post('/base/court/%s/status' % cid, data={'status': 'A'}, follow_redirects=True)
        with app.app_context():
            back = db.query_one('SELECT status FROM court WHERE court_id = %s', (cid,))
        check('场地状态恢复为可用', back and back['status'] == 'A')


def _available_slot(day='2026-10-05', start='09:00', duration=120, court_id=None):
    """找一个不冲突的场地与裁判组合。"""
    with app.app_context():
        courts = services.list_courts(available_only=True)
        referees = services.list_referees(available_only=True)
    court_id = court_id or courts[0]['court_id']
    referee_id = referees[0]['referee_id']
    return court_id, referee_id


def case_schedule():
    print('\n[4] 赛程编排与冲突校验（FR-07 ~ FR-09 / TC-01 ~ TC-04）')
    scheduler = new_client()
    login(scheduler, 'schedule01', '123456')
    r = scheduler.get('/schedule/')
    check('赛程编排页可访问', r.status_code == 200, r.status_code)

    with app.app_context():
        players = db.query_all("SELECT player_id, gender FROM player WHERE gender = 'M' "
                               "ORDER BY player_id LIMIT 4")
        courts = services.list_courts(available_only=True)
        referees = services.list_referees(available_only=True)
        court_a = courts[0]
        court_b = courts[1] if len(courts) > 1 else courts[0]
        ref_a = referees[0]
        ref_b = referees[1] if len(referees) > 1 else referees[0]
        maintenance_court = db.query_one("SELECT court_id FROM court WHERE status = 'M' LIMIT 1")

    if len(players) < 2:
        check('存在可用的男性运动员数据', False, '演示数据不足')
        return

    p1, p2 = players[0]['player_id'], players[1]['player_id']

    # ---- TC-01 正常编排 ----
    form = {
        'event_code': 'MS', 'round_code': 'R1', 'court_id': court_a['court_id'],
        'referee_id': ref_a['referee_id'], 'match_date': '2026-10-05',
        'start_time': '09:00', 'duration_min': '120',
        'team1_players': p1, 'team2_players': p2,
    }
    scheduler.post('/schedule/create', data=form, follow_redirects=True)
    with app.app_context():
        created = db.query_one("SELECT match_id FROM `match` WHERE match_date = '2026-10-05' "
                               "AND start_time = '09:00' AND court_id = %s", (court_a['court_id'],))
    if created:
        CREATED['matches'].append(created['match_id'])
    check('TC-01 正常编排一场比赛保存成功', created is not None)

    # ---- TC-02 选手时间冲突（同选手同时段再排一场，换场地换裁判） ----
    form2 = dict(form)
    form2['court_id'] = court_b['court_id']
    form2['referee_id'] = ref_b['referee_id']
    r = scheduler.post('/schedule/create', data=form2, follow_redirects=True)
    body = r.data.decode()
    with app.app_context():
        dup = db.query_all("SELECT match_id FROM `match` WHERE match_date = '2026-10-05' "
                           "AND start_time = '09:00'")
    check('TC-02 选手时间冲突被拒绝保存', len(dup) == 1, '同日同时段比赛数=%d' % len(dup))
    check('TC-02 页面提示冲突信息', '冲突' in body)

    # ---- TC-04 时段边界：前一场 11:00 结束，下一场 11:00 开始不冲突 ----
    form3 = dict(form)
    form3['start_time'] = '11:00'
    form3['court_id'] = court_a['court_id']
    form3['referee_id'] = ref_a['referee_id']
    scheduler.post('/schedule/create', data=form3, follow_redirects=True)
    with app.app_context():
        boundary = db.query_one("SELECT match_id FROM `match` WHERE match_date = '2026-10-05' "
                                "AND start_time = '11:00' AND court_id = %s", (court_a['court_id'],))
    if boundary:
        CREATED['matches'].append(boundary['match_id'])
    check('TC-04 结束时间与下一场开始时间相同不算冲突', boundary is not None)

    # ---- 场地时段冲突：同场地同时段再排（换选手） ----
    if len(players) >= 4:
        p3, p4 = players[2]['player_id'], players[3]['player_id']
        form4 = dict(form)
        form4['team1_players'] = p3
        form4['team2_players'] = p4
        form4['referee_id'] = ref_b['referee_id']
        r = scheduler.post('/schedule/create', data=form4, follow_redirects=True)
        with app.app_context():
            same_slot = db.query_all("SELECT match_id FROM `match` WHERE match_date = '2026-10-05' "
                                     "AND start_time = '09:00' AND court_id = %s", (court_a['court_id'],))
        check('场地时段冲突被拒绝保存', len(same_slot) == 1, '同场地同时段比赛数=%d' % len(same_slot))

    # ---- TC-03 维护中的场地 ----
    if maintenance_court:
        form5 = dict(form)
        form5['court_id'] = maintenance_court['court_id']
        form5['start_time'] = '15:00'
        r = scheduler.post('/schedule/create', data=form5, follow_redirects=True)
        body = r.data.decode()
        with app.app_context():
            bad = db.query_one("SELECT match_id FROM `match` WHERE court_id = %s "
                               "AND match_date = '2026-10-05' AND start_time = '15:00'",
                               (maintenance_court['court_id'],))
        check('TC-03 维护中场地不能排期', bad is None)
        check('TC-03 页面提示场地维护中', '维护' in body)

    # ---- 赛程表筛选与导出（FR-09） ----
    r = scheduler.get('/schedule/list?date=2026-10-05')
    check('赛程表按日期筛选可访问', r.status_code == 200, r.status_code)
    r = scheduler.get('/schedule/export?date=2026-10-05')
    check('赛程表导出 CSV 成功', r.status_code == 200 and 'csv' in (r.headers.get('Content-Type') or '').lower(),
          r.headers.get('Content-Type'))


def case_results():
    print('\n[5] 成绩录入与统计（FR-10 / FR-11 / TC-05 / TC-06）')
    recorder = new_client()
    login(recorder, 'record01', '123456')
    r = recorder.get('/results/')
    check('成绩录入页可访问', r.status_code == 200, r.status_code)

    with app.app_context():
        match = db.query_one("SELECT * FROM `match` WHERE status = 'P' ORDER BY match_id LIMIT 1")
        if match is None:
            match = db.query_one("SELECT * FROM `match` WHERE status = 'N' ORDER BY match_id LIMIT 1")
    if match is None:
        check('存在未结束比赛数据', False)
        return

    mid = match['match_id']
    # 若不是 P 状态，先点开始比赛
    if match['status'] == 'N':
        recorder.post('/results/%s/start' % mid, follow_redirects=True)
        with app.app_context():
            match = db.query_one("SELECT status FROM `match` WHERE match_id = %s", (mid,))
        check('开始比赛后状态变为进行中', match and match['status'] == 'P')

    with app.app_context():
        before_players = services.fetch_players([mid]).get(mid, [])
        before = {p['player_id']: db.query_one(
            'SELECT win_count, loss_count FROM player WHERE player_id = %s', (p['player_id'],))
            for p in before_players}
        # 记录原始状态，测试结束时还原（QAD-14）
        snap = db.query_one('SELECT status, set_score, game_score, actual_min '
                            'FROM `match` WHERE match_id = %s', (mid,))
        RESTORE['matches'].append((mid, snap))
        for pid, row in before.items():
            RESTORE['players'].append((pid, row['win_count'], row['loss_count']))

    # ---- TC-06 比分格式异常 ----
    r = recorder.post('/results/%s/score' % mid,
                      data={'set_score': '3:0', 'game_score': 'abc', 'actual_min': '95'},
                      follow_redirects=True)
    with app.app_context():
        still = db.query_one('SELECT status, set_score FROM `match` WHERE match_id = %s', (mid,))
    check('TC-06 非法比分格式被拦截', still and still['status'] == 'P' and not still['set_score'])

    # ---- 正常录入 ----
    r = recorder.post('/results/%s/score' % mid,
                      data={'set_score': '2:1', 'game_score': '6:4 3:6 7:5', 'actual_min': '95'},
                      follow_redirects=True)
    with app.app_context():
        done = db.query_one('SELECT status, set_score, game_score, actual_min FROM `match` WHERE match_id = %s', (mid,))
        after = {p['player_id']: db.query_one(
            'SELECT win_count, loss_count FROM player WHERE player_id = %s', (p['player_id'],))
            for p in before_players}
    check('正常录分成功并置为已结束（FR-10）', done and done['status'] == 'F' and done['set_score'] == '2:1')
    check('比分与时长正确落库', done and done['game_score'] == '6:4 3:6 7:5' and int(done['actual_min']) == 95)

    # 胜负场次自动更新（FR-11）：胜方选手 win_count +1
    if done and before:
        winners_updated = False
        for pid, row in before.items():
            now = after[pid]
            if row and now and int(now['win_count']) + int(now['loss_count']) == \
                    int(row['win_count']) + int(row['loss_count']) + 1:
                winners_updated = True
        check('保存比分后自动更新选手胜负场次（FR-11）', winners_updated)

    # ---- TC-05 重复录入 ----
    r = recorder.post('/results/%s/score' % mid,
                      data={'set_score': '2:0', 'game_score': '6:1 6:1', 'actual_min': '60'},
                      follow_redirects=True)
    with app.app_context():
        locked = db.query_one('SELECT set_score FROM `match` WHERE match_id = %s', (mid,))
    check('TC-05 已结束比赛不能重复录入', locked and locked['set_score'] == '2:1')

    # ---- 统计报表 ----
    r = recorder.get('/results/stats')
    check('统计报表页可访问', r.status_code == 200, r.status_code)
    r = recorder.get('/results/stats/export')
    check('统计报表可导出 CSV', r.status_code == 200 and 'csv' in (r.headers.get('Content-Type') or '').lower(),
          r.headers.get('Content-Type'))


def case_system():
    print('\n[6] 系统管理（FR-02）')
    admin = new_client()
    login(admin)
    r = admin.get('/system/staff')
    check('账号管理页可访问', r.status_code == 200, r.status_code)
    r = admin.get('/system/logs')
    check('操作日志页可访问', r.status_code == 200, r.status_code)

    # 新增账号 -> 重置密码 -> 停用
    login_name = 'test_%s' % datetime.now().strftime('%H%M%S')
    r = admin.post('/system/staff/create', data={
        'name': '测试员工', 'post': '测试岗位', 'login_name': login_name,
        'password': '123456', 'role': 'recorder',
    }, follow_redirects=True)
    with app.app_context():
        row = db.query_one('SELECT staff_id, password_hash FROM staff WHERE login_name = %s', (login_name,))
    if row:
        CREATED['staff'].append(row['staff_id'])
    check('新增账号成功（FR-02）', row is not None)
    if row:
        from werkzeug.security import check_password_hash
        check('账号密码使用加盐哈希存储', check_password_hash(row['password_hash'], '123456'))
        admin.post('/system/staff/%s/reset' % row['staff_id'], follow_redirects=True)
        admin.post('/system/staff/%s/toggle' % row['staff_id'], follow_redirects=True)
        with app.app_context():
            disabled = db.query_one('SELECT status FROM staff WHERE staff_id = %s', (row['staff_id'],))
        check('账号可停用', disabled and disabled['status'] == 'D')
        # 停用账号不能登录
        c = new_client()
        r = c.post('/login', data={'login_name': login_name, 'password': '123456'}, follow_redirects=True)
        check('停用账号登录被拒绝', '停用' in r.data.decode())
        admin.post('/system/staff/%s/toggle' % row['staff_id'], follow_redirects=True)
        admin.post('/system/staff/%s/delete' % row['staff_id'], follow_redirects=True)

    with app.app_context():
        log_count = db.query_value('SELECT COUNT(*) AS c FROM sys_log', None, 0)
    check('操作日志有记录', log_count > 0, '日志条数=%d' % log_count)


def case_validation_units():
    print('\n[7] 业务规则单元校验')
    # 时间区间边界
    from app.validation import intervals_overlap
    check('首尾相接不算冲突（TC-04 单元）',
          not intervals_overlap('09:00', 120, '11:00', 120))
    check('重叠 1 分钟算冲突', intervals_overlap('09:00', 120, '10:59', 120))


def main():
    global app
    app = create_app('config')
    print('=' * 70)
    print(' 中网赛事运营管理系统 端到端验收测试')
    print(' 数据库：%s' % app.config['DB_CONFIG'])
    print('=' * 70)

    cases = [
        ('公开查询端', case_public_pages),
        ('登录与权限', case_login_and_permission),
        ('基础数据', case_base_data),
        ('赛程编排', case_schedule),
        ('成绩与统计', case_results),
        ('系统管理', case_system),
        ('业务规则单元', case_validation_units),
    ]
    for title, fn in cases:
        try:
            fn()
        except Exception:
            check('%s 用例执行异常' % title, False)
            traceback.print_exc()

    print('\n[清理] 删除本次测试创建的数据 ...')
    cleanup()

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = total - passed
    print('\n' + '=' * 70)
    print(' 测试结果：%d/%d 通过，%d 失败' % (passed, total, failed))
    if failed:
        print(' 失败用例：')
        for name, ok, detail in RESULTS:
            if not ok:
                print('   - %s %s' % (name, ('(%s)' % detail) if detail else ''))
    print('=' * 70)
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
