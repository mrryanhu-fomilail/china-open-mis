# coding: utf-8
"""
系统配置：数据库连接、会话安全、赛事周期。

配置优先从环境变量读取，其次读取项目根目录下的 .env 文件，
都没有时使用本文件中的默认值。部署到自己的电脑时，只需修改 .env
里的 DB_PASSWORD 即可，不需要改动其他代码。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env(path):
    """极简 .env 解析，避免引入额外依赖。"""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


_ENV = _load_env(os.path.join(BASE_DIR, '.env'))


def _get(key, default=''):
    value = os.environ.get(key)
    if value is not None and value != '':
        return value
    value = _ENV.get(key)
    if value is not None and value != '':
        return value
    return default


# ---------------------------------------------------------------------------
# 数据库连接参数（MySQL 8.0+）
# ---------------------------------------------------------------------------
DB_CONFIG = {
    'host': _get('DB_HOST', '127.0.0.1'),
    'port': int(_get('DB_PORT', '3306')),
    'user': _get('DB_USER', 'root'),
    'password': _get('DB_PASSWORD', ''),
    'database': _get('DB_NAME', 'china_open'),
    'charset': 'utf8mb4',
}

# ---------------------------------------------------------------------------
# 会话与安全
# ---------------------------------------------------------------------------
SECRET_KEY = _get('SECRET_KEY', 'china-open-mis-course-design-2026')
SESSION_TIMEOUT_MIN = int(_get('SESSION_TIMEOUT_MIN', '30'))   # 2.4 节：会话超时 30 分钟
LOGIN_MAX_FAIL = 3                                             # 2.4 节：连续 3 次错误
LOGIN_LOCK_MIN = 10                                            # 2.4 节：锁定 10 分钟

# ---------------------------------------------------------------------------
# 赛事周期（4.5 节：比赛日期须在赛事周期内）
# ---------------------------------------------------------------------------
EVENT_START = _get('EVENT_START', '2026-09-28')
EVENT_END = _get('EVENT_END', '2026-10-11')

# ---------------------------------------------------------------------------
# Flask 运行参数
# ---------------------------------------------------------------------------
HOST = _get('APP_HOST', '127.0.0.1')
PORT = int(_get('APP_PORT', '5050'))  # 5000 在 macOS 上常被隔空播放接收器占用
# 默认关闭调试模式：正式演示时异常只显示友好提示，不暴露 Werkzeug 调试器（QAD-11）
DEBUG = _get('APP_DEBUG', '0') == '1'

# 会话 Cookie 安全属性（QAD-10）
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
