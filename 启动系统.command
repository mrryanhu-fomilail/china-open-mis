#!/bin/bash
# =====================================================================
# 中网赛事运营管理系统  一键启动脚本（macOS 可双击运行）
#   1) 检查 Python 依赖
#   2) 检查数据库；如果连不上（例如忘记了 MySQL 密码），自动启用项目自带的
#      本地 MySQL 实例（数据在 ~/.china_open_mysql，不需要管理员密码）
#   3) 启动网页服务并自动打开浏览器
# =====================================================================
cd "$(dirname "$0")" || exit 1

echo "=============================================================="
echo " 中国网球公开赛赛事运营管理系统（管理信息系统课程设计）"
echo "=============================================================="

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

# ---------- 1. 检查 Python 依赖 ----------
echo "[1/3] 检查 Python 依赖 ..."
if ! $PY -c "import flask, pymysql" >/dev/null 2>&1; then
  echo "      未检测到 Flask / PyMySQL，正在安装 ..."
  if ! $PY -m pip install -r requirements.txt; then
    echo ""
    echo " 依赖安装失败。请手动执行："
    echo "     cd \"$(pwd)\""
    echo "     pip install -r requirements.txt"
    echo ""
    read -r -p "按回车键退出 ..."
    exit 1
  fi
fi
echo "      依赖就绪。"

# ---------- 2. 检查数据库 ----------
# 返回值：0=可连接且已建表；3=能连接但没有演示数据表；2=连不上
check_db() {
  $PY - <<'PY'
import sys
sys.path.insert(0, '.')
import config
try:
    import pymysql
    conn = pymysql.connect(host=config.DB_CONFIG['host'], port=config.DB_CONFIG['port'],
                           user=config.DB_CONFIG['user'],
                           password=config.DB_CONFIG['password'],
                           connect_timeout=2, charset='utf8mb4')
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = 'player'",
                    (config.DB_CONFIG['database'],))
        exists = cur.fetchone()[0] > 0
    conn.close()
    sys.exit(0 if exists else 3)
except Exception:
    sys.exit(2)
PY
}

echo "[2/3] 检查数据库 ..."
check_db
DB_STATE=$?

if [ "$DB_STATE" -ne 0 ]; then
  echo "      当前配置连不上 MySQL（可能是密码未知、MySQL 服务未启动或还没初始化）。"
  echo "      尝试启用项目自带的本地 MySQL 实例（不需要本机 root 密码）..."
  if $PY tools/local_mysql.py start; then
    check_db
    DB_STATE=$?
  else
    echo "      本地实例也没能启动，改为按 .env 中的配置初始化。"
  fi
fi

if [ "$DB_STATE" -eq 3 ]; then
  echo "      正在初始化数据库（建表 + 导入演示数据，只需一次）..."
  if ! $PY tools/init_db.py; then
    echo ""
    echo " 初始化失败。请按上面的提示排查。"
    read -r -p "按回车键退出 ..."
    exit 1
  fi
elif [ "$DB_STATE" -ne 0 ]; then
  echo ""
  echo " 数据库仍然连不上。如果你知道本机 MySQL 的密码，可以按提示输入："
  if ! $PY tools/init_db.py; then
    echo ""
    echo " 初始化失败。请按上面的提示排查（MySQL 是否启动、密码是否正确）。"
    read -r -p "按回车键退出 ..."
    exit 1
  fi
fi
echo "      数据库就绪。"
$PY - <<'PY'
import sys
sys.path.insert(0, '.')
import config
c = config.DB_CONFIG
print('      连接参数：%s@%s:%s/%s' % (c['user'], c['host'], c['port'], c['database']))
PY

# ---------- 3. 启动服务 ----------
APP_PORT=$($PY -c "import config; print(config.PORT)" 2>/dev/null || echo 5050)
echo "[3/3] 启动系统，浏览器会自动打开 http://127.0.0.1:${APP_PORT}"
echo "      提示：本窗口不要关闭，关闭窗口即停止系统。"
echo "--------------------------------------------------------------"
( sleep 2; open "http://127.0.0.1:${APP_PORT}" >/dev/null 2>&1 ) &
$PY app.py

read -r -p "服务已结束，按回车键关闭窗口 ..."
