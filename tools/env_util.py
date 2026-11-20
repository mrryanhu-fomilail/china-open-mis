# coding: utf-8
"""读写项目根目录 .env 的小工具（init_db.py 与 local_mysql.py 共用）。"""
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, '.env')


def read_env(path=None):
    """读取 .env 为 dict；文件不存在返回空 dict。"""
    path = path or ENV_PATH
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


def update_env(updates, path=None):
    """把若干键写回 .env，保留文件里的注释和其它配置；不存在的键追加到末尾。"""
    path = path or ENV_PATH
    lines = []
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()

    seen = set()
    for index, line in enumerate(lines):
        match = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
        if match and match.group(1) in updates:
            lines[index] = '%s=%s' % (match.group(1), updates[match.group(1)])
            seen.add(match.group(1))
    for key, value in updates.items():
        if key not in seen:
            lines.append('%s=%s' % (key, value))

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path
