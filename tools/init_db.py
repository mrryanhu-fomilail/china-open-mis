# coding: utf-8
"""
一键初始化数据库：建库、建表、导入演示数据、创建演示账号。

用法（在项目根目录执行）：
    python tools/init_db.py

    首次运行时如果连接 MySQL 被拒绝（密码不对），程序会提示你输入本机 MySQL
    的密码，校验通过后自动写入项目根目录的 .env，之后 python app.py 直接可用。

其它参数（可选）：
    python tools/init_db.py --password 123456     # 直接给出密码，跳过提示
    python tools/init_db.py --port 3307           # 指定端口
    python tools/init_db.py --user root           # 指定用户名
    python tools/init_db.py --database china_open # 指定库名
    python tools/init_db.py --no-input            # 不提示输入密码（交给脚本/CI 用）

执行顺序：
    1. 连接 MySQL（不指定库），执行 CREATE DATABASE IF NOT EXISTS；
    2. 执行 sql/01_schema.sql（建表，会先 DROP 旧表）；
    3. 执行 sql/02_seed.sql（导入演示数据）；
    4. 用加盐哈希写入三个演示账号（密码 123456）。
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_util import ENV_PATH, read_env, update_env   # noqa: E402

try:
    import pymysql
    from werkzeug.security import generate_password_hash
except ImportError as exc:  # pragma: no cover - 仅用于首次环境不完整时的提示
    print('缺少 Python 依赖：%s' % exc)
    print('请先执行：pip install -r requirements.txt')
    raise SystemExit(1)

import config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.path.join(BASE_DIR, 'sql')
ENV_PATH = os.path.join(BASE_DIR, '.env')

DEMO_STAFF = [
    ('S0001', '张启明', '系统管理员', 'admin01', '123456', 'admin'),
    ('S0002', '李编排', '赛事编排员', 'schedule01', '123456', 'scheduler'),
    ('S0003', '王录入', '成绩录入员', 'record01', '123456', 'recorder'),
]

ACCESS_DENIED_CODES = (1045, 1698)   # 1045: 密码错误；1698: 该用户无权限


# ---------------------------------------------------------------------------
# 读取密码
# ---------------------------------------------------------------------------
def ask_password(prompt):
    """读取密码：优先隐藏输入；没有终端时退回普通输入（便于管道测试）。"""
    try:
        return getpass.getpass(prompt)
    except Exception:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        line = sys.stdin.readline()
        return (line or '').rstrip('\n')


# ---------------------------------------------------------------------------
# 数据库连接（密码不对时引导用户输入并写入 .env）
# ---------------------------------------------------------------------------
def open_connection(args, password=None):
    return pymysql.connect(
        host=args.host, port=args.port, user=args.user,
        password=args.password if password is None else password,
        charset='utf8mb4', autocommit=False,
    )


def connect_with_prompt(args):
    try:
        return open_connection(args), False
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        if code not in ACCESS_DENIED_CODES:
            raise
        if args.no_input:
            print('  [失败] 连接被拒绝：%s' % exc)
            print('  请用 --password 指定密码，或先修改 .env 中的 DB_PASSWORD。')
            raise SystemExit(1)
        print('  [提示] 连接 MySQL 被拒绝，需要输入密码（只用于本机，会保存到 .env）。')
        for attempt in range(3):
            password = ask_password('请输入 MySQL 用户 %s 的密码（回车确认）：' % args.user)
            try:
                conn = open_connection(args, password=password)
            except pymysql.err.OperationalError as exc2:
                code2 = exc2.args[0] if exc2.args else None
                if code2 in ACCESS_DENIED_CODES:
                    print('  [错误] 密码不正确，请再试一次（还剩 %d 次）。' % (2 - attempt))
                    continue
                raise
            args.password = password
            update_env({
                'DB_HOST': args.host,
                'DB_PORT': str(args.port),
                'DB_USER': args.user,
                'DB_PASSWORD': password,
                'DB_NAME': args.database,
            })
            print('  [完成] 密码验证通过，已写入 %s' % ENV_PATH)
            return conn, True
        raise SystemExit('  [失败] 密码错误次数过多，已退出。')


# ---------------------------------------------------------------------------
# SQL 脚本执行
# ---------------------------------------------------------------------------
def split_sql(text):
    """把 SQL 脚本拆成单条语句（跳过注释，正确处理引号内的分号）。"""
    statements, buf = [], []
    in_str, quote = False, ''
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:   # '' 转义
                    buf.append(text[i + 1])
                    i += 1
                else:
                    in_str = False
        else:
            if ch in ("'", '"'):
                in_str, quote = True, ch
                buf.append(ch)
            elif ch == '-' and text[i:i + 2] == '--':
                while i < len(text) and text[i] != '\n':
                    i += 1
                continue
            elif ch == '#':
                while i < len(text) and text[i] != '\n':
                    i += 1
                continue
            elif ch == ';':
                stmt = ''.join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
            else:
                buf.append(ch)
        i += 1
    tail = ''.join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def run_sql_file(conn, filename, label, database):
    """执行 SQL 脚本。

    脚本里自带的 CREATE DATABASE / USE 语句会被跳过（这样 --database 参数才有效），
    执行完再把连接切回目标库，避免后续语句写错库。
    """
    path = os.path.join(SQL_DIR, filename)
    if not os.path.exists(path):
        print('  [跳过] 未找到 %s' % path)
        return 0
    with open(path, 'r', encoding='utf-8') as f:
        statements = split_sql(f.read())
    count = 0
    with conn.cursor() as cur:
        for stmt in statements:
            low = stmt.strip().lower()
            if low.startswith('use ') or low.startswith('create database'):
                continue
            cur.execute(stmt)
            count += 1
        cur.execute('USE `%s`' % database)
    conn.commit()
    print('  [完成] %s：执行 %d 条语句' % (label, count))
    return count


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    env = read_env()
    parser = argparse.ArgumentParser(description='初始化中网赛事运营管理系统数据库')
    parser.add_argument('--host', default=env.get('DB_HOST') or config.DB_CONFIG['host'])
    parser.add_argument('--port', type=int,
                        default=int(env.get('DB_PORT') or config.DB_CONFIG['port']))
    parser.add_argument('--user', default=env.get('DB_USER') or config.DB_CONFIG['user'])
    parser.add_argument('--password',
                        default=env.get('DB_PASSWORD') if 'DB_PASSWORD' in env
                        else config.DB_CONFIG['password'])
    parser.add_argument('--database',
                        default=env.get('DB_NAME') or config.DB_CONFIG['database'])
    parser.add_argument('--no-input', action='store_true',
                        help='连接失败时不提示输入密码')
    args = parser.parse_args()

    print('=' * 68)
    print(' 初始化数据库：%s@%s:%s' % (args.user, args.host, args.port))
    print('=' * 68)

    try:
        conn, prompted = connect_with_prompt(args)
    except pymysql.err.OperationalError as exc:
        print(' [失败] 无法连接 MySQL：%s' % exc)
        print(' 请检查：1) MySQL 服务是否已启动；')
        print('         2) 端口是否正确（默认 3306，可用 --port 指定）；')
        print('         3) 用户名/密码是否正确（可在 .env 里改，或用 --password 指定）。')
        return 1

    # 连接成功后把实际生效的连接参数写回 .env，保证 python app.py 用的是同一套配置
    update_env({
        'DB_HOST': args.host,
        'DB_PORT': str(args.port),
        'DB_USER': args.user,
        'DB_PASSWORD': args.password,
        'DB_NAME': args.database,
    })

    try:
        with conn.cursor() as cur:
            cur.execute('CREATE DATABASE IF NOT EXISTS `%s` DEFAULT CHARACTER SET utf8mb4 '
                        'COLLATE utf8mb4_general_ci' % args.database)
            cur.execute('USE `%s`' % args.database)
        conn.commit()
        print('  [完成] 数据库 %s 已就绪' % args.database)

        run_sql_file(conn, '01_schema.sql', '建表脚本 01_schema.sql', args.database)
        run_sql_file(conn, '02_seed.sql', '演示数据 02_seed.sql', args.database)

        # 演示账号（密码使用 pbkdf2 加盐哈希，不存明文）
        with conn.cursor() as cur:
            for staff_id, name, post, login_name, password, role in DEMO_STAFF:
                cur.execute(
                    'INSERT INTO staff (staff_id, name, post, login_name, password_hash, role, status) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s) '
                    'ON DUPLICATE KEY UPDATE name = VALUES(name), post = VALUES(post), '
                    'password_hash = VALUES(password_hash), role = VALUES(role), status = %s, '
                    'fail_count = 0, locked_until = NULL',
                    (staff_id, name, post, login_name,
                     generate_password_hash(password), role, 'A', 'A'),
                )
        conn.commit()
        print('  [完成] 演示账号已创建（密码均为 123456）')

        # 统计各表记录数
        with conn.cursor() as cur:
            print('-' * 68)
            for table in ('staff', 'player', 'referee', 'court', 'match', 'match_player', 'sys_log'):
                cur.execute('SELECT COUNT(*) FROM `%s`' % table)
                print('  %-14s %5d 条' % (table, cur.fetchone()[0]))
    finally:
        conn.close()

    print('-' * 68)
    print(' 初始化完成，启动系统：python app.py')
    print(' 演示账号：admin01（管理员）/ schedule01（编排员）/ record01（录入员），密码 123456')
    print('=' * 68)
    return 0


if __name__ == '__main__':
    sys.exit(main())
