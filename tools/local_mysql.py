# coding: utf-8
"""
项目自带的本地 MySQL 实例管理（不需要本机 MySQL 的 root 密码）。

背景：如果忘记本机 MySQL 的 root 密码，又不想用管理员权限重置，可以用这个脚本
在项目之外（默认 ~/.china_open_mysql）初始化并启动一个独立 MySQL 实例，
监听 127.0.0.1:3307，root 无密码，专门给本系统使用。

用法：
    python tools/local_mysql.py start     # 初始化（首次）并启动，同时把连接参数写进 .env
    python tools/local_mysql.py stop      # 停止实例
    python tools/local_mysql.py status    # 查看是否在运行
    python tools/local_mysql.py start --port 3308   # 换端口

注意：
    数据目录在用户主目录下（默认 ~/.china_open_mysql），不会进入项目文件夹，
    也不会影响本机原有的 MySQL（3306）服务和数据。
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_util import update_env   # noqa: E402

DEFAULT_PORT = 3307
DEFAULT_BASE = os.path.join(os.path.expanduser('~'), '.china_open_mysql')


# ---------------------------------------------------------------------------
# 查找 mysqld
# ---------------------------------------------------------------------------
def find_mysqld():
    candidates = [
        shutil.which('mysqld'),
        '/opt/anaconda3/bin/mysqld',
        '/usr/local/mysql/bin/mysqld',
        '/opt/homebrew/bin/mysqld',
        '/usr/local/bin/mysqld',
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def find_basedir(mysqld):
    """由 mysqld 路径反推 MySQL 安装目录（basedir）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(mysqld)))


def find_lc_messages(basedir):
    for name in ('mysql-8.4', 'mysql-8.0', 'mysql'):
        path = os.path.join(basedir, 'share', name)
        if os.path.isdir(path):
            return path
    path = os.path.join(basedir, 'share')
    return path if os.path.isdir(path) else None


# ---------------------------------------------------------------------------
# 运行状态
# ---------------------------------------------------------------------------
def port_open(port, host='127.0.0.1', timeout=0.5):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def can_login(port, host='127.0.0.1'):
    """用 root 空密码试连一次，能连上说明实例已就绪。"""
    try:
        import pymysql
        conn = pymysql.connect(host=host, port=port, user='root', password='',
                               connect_timeout=1, charset='utf8mb4')
        conn.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 启动 / 停止
# ---------------------------------------------------------------------------
def start(args):
    mysqld = find_mysqld()
    if mysqld is None:
        print(' [失败] 没有找到 mysqld 可执行文件。')
        print(' 说明：可以使用 Anaconda 自带的 MySQL（/opt/anaconda3/bin/mysqld），')
        print(' 或在终端执行：conda install -c conda-forge mysql 后重试。')
        return 1

    if port_open(args.port) and can_login(args.port):
        print(' [就绪] 本地 MySQL 实例已在运行（127.0.0.1:%d，root 无密码）。' % args.port)
        write_env(args.port)
        return 0
    if port_open(args.port):
        print(' [失败] 端口 %d 已被其它程序占用，请换端口：--port 3308' % args.port)
        return 1

    basedir = find_basedir(mysqld)
    data_dir = os.path.join(args.datadir, 'data')
    run_dir = os.path.join(args.datadir, 'run')
    os.makedirs(run_dir, exist_ok=True)
    socket_path = os.path.join(run_dir, 'mysql.sock')
    pid_path = os.path.join(run_dir, 'mysqld.pid')
    error_log = os.path.join(run_dir, 'mysqld.err')
    lc_dir = find_lc_messages(basedir)

    # 首次使用：初始化数据目录（root 空密码）
    if not os.path.isdir(data_dir) or not os.listdir(data_dir):
        print(' [步骤] 首次使用，正在初始化数据目录：%s' % data_dir)
        cmd = [mysqld, '--initialize-insecure', '--datadir=%s' % data_dir,
               '--basedir=%s' % basedir, '--log-error=%s' % error_log]
        if lc_dir:
            cmd.append('--lc-messages-dir=%s' % lc_dir)
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        if result.returncode != 0:
            print(' [失败] 初始化失败，详见日志：%s' % error_log)
            tail_log(error_log)
            return 1
        print(' [完成] 数据目录初始化完成。')

    # 后台启动 mysqld
    print(' [步骤] 启动本地 MySQL 实例（端口 %d）...' % args.port)
    cmd = [
        mysqld,
        '--datadir=%s' % data_dir,
        '--basedir=%s' % basedir,
        '--port=%d' % args.port,
        '--socket=%s' % socket_path,
        '--pid-file=%s' % pid_path,
        '--log-error=%s' % error_log,
        '--mysqlx=0',
    ]
    if lc_dir:
        cmd.append('--lc-messages-dir=%s' % lc_dir)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)

    for _ in range(60):
        time.sleep(0.5)
        if can_login(args.port):
            print(' [完成] 本地 MySQL 已启动：127.0.0.1:%d（root 无密码）' % args.port)
            print('        数据目录：%s' % data_dir)
            write_env(args.port)
            return 0
    print(' [失败] 等待启动超时，详见日志：%s' % error_log)
    tail_log(error_log)
    return 1


def write_env(port):
    path = update_env({
        'DB_HOST': '127.0.0.1',
        'DB_PORT': str(port),
        'DB_USER': 'root',
        'DB_PASSWORD': '',
        'DB_NAME': 'china_open',
    })
    print(' [完成] 已把连接参数写入 %s' % path)


def stop(args):
    pid_path = os.path.join(args.datadir, 'run', 'mysqld.pid')
    if not port_open(args.port) and not os.path.exists(pid_path):
        print(' [提示] 本地 MySQL 实例没有在运行。')
        return 0
    if os.path.exists(pid_path):
        try:
            with open(pid_path, 'r') as f:
                pid = int(f.read().strip())
            os.kill(pid, 15)   # SIGTERM，MySQL 会正常关闭
        except Exception as exc:
            print(' [提示] 按 pid 文件停止失败：%s' % exc)
    for _ in range(20):
        time.sleep(0.5)
        if not port_open(args.port):
            print(' [完成] 本地 MySQL 实例已停止。')
            return 0
    print(' [提示] 实例可能仍在运行，可手动执行：kill $(cat %s)' % pid_path)
    return 1


def status(args):
    running = port_open(args.port) and can_login(args.port)
    if running:
        print(' 本地 MySQL 实例：运行中（127.0.0.1:%d，root 无密码）' % args.port)
        print(' 数据目录：%s' % os.path.join(args.datadir, 'data'))
        return 0
    print(' 本地 MySQL 实例：未运行（端口 %d）' % args.port)
    return 3


def tail_log(path, lines=8):
    try:
        with open(path, 'r', errors='ignore') as f:
            content = f.read().splitlines()
        print(' 日志末尾：')
        for line in content[-lines:]:
            print('   ' + line)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description='项目自带本地 MySQL 实例管理')
    parser.add_argument('action', choices=['start', 'stop', 'status'],
                        help='start=初始化并启动, stop=停止, status=查看状态')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--datadir', default=DEFAULT_BASE,
                        help='数据目录（默认 ~/.china_open_mysql，不放在项目里）')
    args = parser.parse_args()

    if args.action == 'start':
        return start(args)
    if args.action == 'stop':
        return stop(args)
    return status(args)


if __name__ == '__main__':
    sys.exit(main())
