# coding: utf-8
"""
数据库访问层：对 pymysql 的轻量封装。

对外提供四个函数 + 一个事务上下文：
    query_all(sql, params)   查询多行，返回 list[dict]
    query_one(sql, params)   查询一行，返回 dict 或 None
    query_value(sql, params) 查询单个值
    execute(sql, params)     执行写操作（默认自动提交）
    transaction()            多步写操作的原子事务

连接按请求复用（存放在 flask.g 中），请求结束时统一提交/回滚并关闭。
表名 match 是 MySQL 保留字，SQL 中必须写成 `match`。
"""
import pymysql
from pymysql.cursors import DictCursor
from flask import current_app, g


def get_connection():
    """获取当前请求的数据库连接（不存在则创建）。"""
    conn = getattr(g, '_db_conn', None)
    if conn is None:
        cfg = current_app.config['DB_CONFIG']
        conn = pymysql.connect(
            host=cfg['host'],
            port=cfg['port'],
            user=cfg['user'],
            password=cfg['password'],
            database=cfg['database'],
            charset=cfg.get('charset', 'utf8mb4'),
            cursorclass=DictCursor,
            autocommit=False,
        )
        g._db_conn = conn
    return conn


def test_connection(cfg):
    """独立于请求上下文测试连接，供启动自检使用。"""
    conn = pymysql.connect(
        host=cfg['host'], port=cfg['port'], user=cfg['user'],
        password=cfg['password'], database=cfg['database'],
        charset=cfg.get('charset', 'utf8mb4'), cursorclass=DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 AS ok')
            return cur.fetchone()
    finally:
        conn.close()


def close_connection(exc=None):
    """请求结束时的清理：正常提交、异常回滚。"""
    conn = getattr(g, '_db_conn', None)
    if conn is None:
        return
    try:
        if exc is None:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        finally:
            g._db_conn = None


def _in_transaction():
    return getattr(g, '_in_tx', False)


def query_all(sql, params=None):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return list(cur.fetchall())


def query_one(sql, params=None):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


def query_value(sql, params=None, default=None):
    row = query_one(sql, params)
    if not row:
        return default
    return list(row.values())[0]


def execute(sql, params=None):
    """执行 INSERT / UPDATE / DELETE，返回受影响行数。"""
    conn = get_connection()
    with conn.cursor() as cur:
        affected = cur.execute(sql, params or ())
    if not _in_transaction():
        conn.commit()
    return affected


def execute_many(sql, seq_of_params):
    conn = get_connection()
    with conn.cursor() as cur:
        affected = cur.executemany(sql, seq_of_params)
    if not _in_transaction():
        conn.commit()
    return affected


class _Transaction:
    def __enter__(self):
        self.conn = get_connection()
        self.prev = getattr(g, '_in_tx', False)
        g._in_tx = True
        self.cur = self.conn.cursor()
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.cur.close()
            g._in_tx = self.prev
        return False


def transaction():
    """用法：

        with db.transaction() as cur:
            cur.execute('UPDATE ...')
            cur.execute('INSERT ...')
        # 全部成功才提交，任何一步异常整体回滚
    """
    return _Transaction()
