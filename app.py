# coding: utf-8
"""
中国网球公开赛赛事运营管理系统 —— 启动入口

用法：
    python app.py              # 使用 .env / 环境变量中的配置启动
    python app.py --port 5001  # 临时指定端口

启动前请先完成：
    1. 启动本机 MySQL 服务；
    2. 复制 .env.example 为 .env，填写本机 MySQL 密码；
    3. 执行 python tools/init_db.py 建库建表并导入演示数据。
"""
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pymysql  # noqa: F401
except ImportError:
    print('缺少依赖 PyMySQL，请先在当前 Python 环境执行：')
    print('    pip install -r requirements.txt')
    print('（如果 pip 提示权限不足，可加 --user，或改用 Anaconda 自带的 Python 环境）')
    raise SystemExit(1)

import config  # noqa: E402
from app import create_app  # noqa: E402
from app import db as db_module  # noqa: E402

application = create_app('config')
app = application


def _find_free_port(host, port, tries=10):
    """端口被占用时（macOS 的 5000 常被隔空播放接收器占用）自动往后找一个可用端口。"""
    for offset in range(tries):
        candidate = port + offset
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, candidate))
            return candidate
        except OSError:
            continue
        finally:
            sock.close()
    return None


def _print_banner(host, port):
    cfg = config.DB_CONFIG
    print('=' * 68)
    print(' 中国网球公开赛赛事运营管理系统（管理信息系统课程设计）')
    print('=' * 68)
    print(' 数据库   : %s@%s:%s/%s' % (cfg['user'], cfg['host'], cfg['port'], cfg['database']))
    try:
        db_module.test_connection(cfg)
        print(' 连接状态 : 成功')
    except Exception as exc:  # 启动期只提示，不中断（浏览器里会有更友好的提示页）
        print(' 连接状态 : 失败 -> %s' % exc)
        print(' 处理办法 : 检查 MySQL 服务是否启动、.env 中 DB_PASSWORD 是否正确，')
        print('            并确认已执行 python tools/init_db.py')
    print(' 访问地址 : http://%s:%d' % (host, port))
    print(' 演示账号 : admin01 / schedule01 / record01  密码均为 123456')
    print('=' * 68)


if __name__ == '__main__':
    host = config.HOST
    port = config.PORT
    if '--port' in sys.argv:
        port = int(sys.argv[sys.argv.index('--port') + 1])
    if '--host' in sys.argv:
        host = sys.argv[sys.argv.index('--host') + 1]

    free_port = _find_free_port(host, port)
    if free_port is None:
        print('端口 %d 及其后 10 个端口都被占用，请用 python app.py --port 端口号 指定其它端口。' % port)
        raise SystemExit(1)
    if free_port != port:
        print('提示：端口 %d 已被其它程序占用，自动改用 %d。' % (port, free_port))
        port = free_port

    _print_banner(host, port)
    app.run(host=host, port=port, debug=config.DEBUG, use_reloader=False)
