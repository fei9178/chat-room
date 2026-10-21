#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Termux 聊天室后端 (单文件) — Render 部署版
=============================================
改动点 (相对原版):
  1. 端口改读环境变量 PORT (Render 注入)
  2. 数据目录改读环境变量 DATA_DIR, 默认 ./data
  3. 持久化从 CSV 改为 SQLite (文件落 DATA_DIR, 需挂载 Render 磁盘)
其余 API / SSE / 前端接口与 app.js、admin.html 完全兼容, 前端无需任何修改.

依赖: 仅标准库 (Python 3.7+)
运行: python3 chat_server.py
"""

import http.server
import socketserver
import json
import os
import re
import socket
import threading
import time
import hashlib
import sqlite3
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# ============ 配置 ============
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.environ.get("WEB_DIR", os.path.join(BASE_DIR, "web"))   # 前端文件目录
DB_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))  # 数据目录
DB_FILE = os.path.join(DB_DIR, os.environ.get("DB_FILE", "chat.db").lstrip("/"))

ADMIN_USER = "admin"                             # 初始管理员账号
ADMIN_PASS = "admin123"                          # 首次登录后请尽快修改

os.makedirs(WEB_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

# ============ 数据库 (SQLite) ============
class DB:
    """用户表 + 消息表, 一条连接一个线程, 短连接够用"""

    def __init__(self, dbfile):
        self.dbfile = dbfile
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self):
        c = sqlite3.connect(self.dbfile)
        c.row_factory = sqlite3.Row
        return c

    def _init_schema(self):
        with self._lock:
            c = self._conn()
            c.execute("""CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                nickname TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time TEXT NOT NULL,
                type TEXT NOT NULL,
                "from" TEXT NOT NULL,
                "to" TEXT NOT NULL,
                content TEXT NOT NULL
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_messages_type_to "
                      "ON messages(type, \"to\")")
            # 首次运行写入默认管理员
            row = c.execute("SELECT 1 FROM users WHERE username=?", (ADMIN_USER,)).fetchone()
            if not row:
                c.execute("INSERT INTO users(username,password_hash,nickname,is_admin) "
                          "VALUES(?,?,?,1)",
                          (ADMIN_USER, hashlib.sha256(ADMIN_PASS.encode()).hexdigest(), "管理员"))
            c.commit()
            c.close()

    @staticmethod
    def hash_pwd(pwd):
        return hashlib.sha256(pwd.encode()).hexdigest()

    def register(self, username, password, nickname=""):
        username = username.strip().lower()
        if not re.match(r"^[a-zA-Z0-9_]{3,20}$", username):
            return False, "账号只能含字母/数字/下划线, 长度3-20"
        if len(password) < 4:
            return False, "密码至少4位"
        with self._lock:
            c = self._conn()
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                c.close()
                return False, "账号已存在"
            c.execute("INSERT INTO users(username,password_hash,nickname) VALUES(?,?,?)",
                      (username, self.hash_pwd(password), nickname or username))
            c.commit()
            c.close()
        return True, "注册成功"

    def login(self, username, password):
        username = username.strip().lower()
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            c.close()
        if not row:
            return False, "账号不存在", None
        if row["banned"]:
            return False, "该账号已被封禁", None
        if row["password_hash"] != self.hash_pwd(password):
            return False, "密码错误", None
        return True, "登录成功", {
            "username": row["username"],
            "nickname": row["nickname"],
            "is_admin": bool(row["is_admin"]),
        }

    def list_users(self):
        with self._lock:
            c = self._conn()
            rows = c.execute("SELECT username,nickname,is_admin,banned FROM users").fetchall()
            c.close()
        return [{"username": r["username"], "nickname": r["nickname"],
                 "is_admin": bool(r["is_admin"]), "banned": bool(r["banned"])} for r in rows]

    def ban(self, username, ban=True):
        username = username.strip().lower()
        with self._lock:
            c = self._conn()
            r = c.execute("UPDATE users SET banned=? WHERE username=? AND is_admin=0",
                          (1 if ban else 0, username))
            ok = r.rowcount > 0
            c.commit()
            c.close()
        return ok

    def set_nickname(self, username, nickname):
        with self._lock:
            c = self._conn()
            r = c.execute("UPDATE users SET nickname=? WHERE username=?",
                          (nickname[:20], username.strip().lower()))
            ok = r.rowcount > 0
            c.commit()
            c.close()
        return ok

    def save_msg(self, mtype, frm, to, content):
        with self._lock:
            c = self._conn()
            c.execute("INSERT INTO messages(time,type,\"from\",\"to\",content) VALUES(?,?,?,?,?)",
                      (datetime.now().isoformat(timespec="seconds"), mtype, frm, to, content))
            c.commit()
            c.close()

    def history(self, limit=300):
        with self._lock:
            c = self._conn()
            rows = c.execute("SELECT time,type,\"from\",\"to\",content FROM messages "
                            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            c.close()
        return [dict(r) for r in reversed(rows)]

db = DB(DB_FILE)

# ============ 在线连接 & 消息分发 ============
class Client:
    def __init__(self, username, send):
        self.username = username
        self.nickname = db._conn().execute(
            "SELECT nickname FROM users WHERE username=?", (username,)).fetchone()[0]
        self.send = send
        self.rooms = set(["public"])

clients_lock = threading.Lock()
clients = {}        # username -> Client
rooms = {"public": "公共大厅"}
rooms_lock = threading.Lock()

def broadcast(event, room=None, skip=None):
    dead = []
    with clients_lock:
        targets = list(clients.values()) if room is None else \
                  [c for c in clients.values() if room in c.rooms]
        for c in targets:
            if c.username == skip:
                continue
            try:
                c.send(event)
            except Exception:
                dead.append(c.username)
    if dead:
        with clients_lock:
            for u in dead:
                clients.pop(u, None)

def online_list():
    with clients_lock:
        return [{"username": c.username, "nickname": c.nickname,
                 "rooms": list(c.rooms)} for c in clients.values()]

def rooms_list():
    with rooms_lock:
        return [{"id": k, "name": v} for k, v in rooms.items()]

# ============ SSE (Server-Sent Events) 长连接 ============
def handle_sse(username, wfile):
    client = Client(username, None)
    q = []
    q_lock = threading.Lock()
    new_data = threading.Event()

    def send(ev):
        with q_lock:
            q.append(ev)
        new_data.set()

    client.send = send
    with clients_lock:
        clients.pop(username, None)
        clients[username] = client

    for r in db.history(300):
        if r["type"] == "group" and r["to"] in client.rooms:
            send({"t": "msg", "mtype": "group", "user": r["from"],
                  "room": r["to"], "text": r["content"], "time": r["time"]})
        elif r["type"] == "pm" and r["to"] == username:
            send({"t": "msg", "mtype": "pm", "user": r["from"],
                  "text": r["content"], "time": r["time"]})

    send({"t": "hello", "user": username, "nickname": client.nickname,
          "rooms": rooms_list(), "online": online_list()})
    broadcast({"t": "online", "online": online_list()})

    wfile.write(b"HTTP/1.1 200 OK\r\n")
    wfile.write(b"Content-Type: text/event-stream\r\n")
    wfile.write(b"Cache-Control: no-cache\r\n")
    wfile.write(b"Connection: keep-alive\r\n")
    wfile.write(b"Access-Control-Allow-Origin: *\r\n\r\n")
    wfile.flush()

    try:
        while True:
            new_data.wait(timeout=15)
            new_data.clear()
            with q_lock:
                batch = q[:]; q.clear()
            for ev in batch:
                line = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                wfile.write(line.encode("utf-8"))
            wfile.write(b": ping\n\n")
            wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        with clients_lock:
            if clients.get(username) is client:
                clients.pop(username, None)
        broadcast({"t": "online", "online": online_list()})

# ============ HTTP 请求处理 ============
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "TermuxChat/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _send_file(self, path):
        ext = os.path.splitext(path)[1]
        ctype = {"html": "text/html", "js": "application/javascript",
                 "css": "text/css", "png": "image/png", "svg": "image/svg+xml",
                 "json": "application/json", "ico": "image/x-icon"}.get(
            ext[1:], "application/octet-stream")
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._json(404, {"ok": False, "msg": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        qs = parse_qs(u.query)

        if path == "/" or path == "/index.html":
            return self._send_file(os.path.join(WEB_DIR, "index.html"))
        if path == "/admin.html":
            return self._send_file(os.path.join(WEB_DIR, "admin.html"))
        if path == "/app.js":
            return self._send_file(os.path.join(WEB_DIR, "app.js"))
        if path.startswith("/api/"):
            return self._api_get(path, qs)

        fpath = os.path.join(WEB_DIR, path.lstrip("/"))
        if os.path.isfile(fpath):
            return self._send_file(fpath)
        self._send_file(os.path.join(WEB_DIR, "index.html"))  # SPA 兜底

    def do_POST(self):
        u = urlparse(self.path)
        body = self._read_json()
        if u.path.startswith("/api/"):
            return self._api_post(u.path, body)
        self._json(404, {"ok": False, "msg": "not found"})

    def _api_get(self, path, qs):
        if path == "/api/stream":
            user = (qs.get("user") or [""])[0]
            if not user or not db._conn().execute("SELECT 1 FROM users WHERE username=?", (user,)).fetchone():
                return self._json(403, {"ok": False, "msg": "请先登录"})
            return handle_sse(user, self.wfile)

        if path == "/api/online":
            return self._json(200, {"ok": True, "online": online_list()})

        if path == "/api/rooms":
            return self._json(200, {"ok": True, "rooms": rooms_list()})

        if path == "/api/history":
            room = (qs.get("room") or ["public"])[0]
            return self._json(200, {"ok": True, "messages": [
                r for r in db.history(300)
                if r["type"] == "group" and r["to"] == room
            ]})

        if path == "/api/admin/users":
            user = (qs.get("user") or [""])[0]
            if not db._conn().execute("SELECT 1 FROM users WHERE username=? AND is_admin=1", (user,)).fetchone():
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            return self._json(200, {"ok": True, "users": db.list_users(),
                                    "online": online_list()})

        self._json(404, {"ok": False, "msg": "not found"})

    def _api_post(self, path, b):
        if path == "/api/register":
            ok, msg = db.register(b.get("username", ""), b.get("password", ""), b.get("nickname", ""))
            return self._json(200 if ok else 400, {"ok": ok, "msg": msg})

        if path == "/api/login":
            ok, msg, info = db.login(b.get("username", ""), b.get("password", ""))
            return self._json(200 if ok else 400, {"ok": ok, "msg": msg, "user": info or None})

        if path == "/api/send":
            user, text, room = b.get("user", ""), (b.get("text") or "").strip(), b.get("room", "public")
            conn = db._conn()
            row = conn.execute("SELECT banned FROM users WHERE username=?", (user,)).fetchone()
            conn.close()
            if not user or not row:
                return self._json(403, {"ok": False, "msg": "请先登录"})
            if row["banned"]:
                return self._json(403, {"ok": False, "msg": "账号已被封禁"})
            if not text:
                return self._json(400, {"ok": False, "msg": "消息为空"})
            if len(text) > 2000:
                return self._json(400, {"ok": False, "msg": "消息过长"})
            db.save_msg("group", user, room, text)
            broadcast({"t": "msg", "mtype": "group", "user": user, "room": room,
                       "text": text, "time": datetime.now().isoformat(timespec="seconds")})
            return self._json(200, {"ok": True})

        if path == "/api/pm":
            user, to, text = b.get("user", ""), b.get("to", ""), (b.get("text") or "").strip()
            if not user or not to or not text:
                return self._json(400, {"ok": False, "msg": "参数缺失"})
            if not db._conn().execute("SELECT 1 FROM users WHERE username=?", (to,)).fetchone():
                return self._json(400, {"ok": False, "msg": "对方不存在"})
            db.save_msg("pm", user, to, text)
            ev = {"t": "msg", "mtype": "pm", "user": user, "text": text,
                  "time": datetime.now().isoformat(timespec="seconds")}
            with clients_lock:
                for u in (to, user):
                    if u in clients:
                        try:
                            clients[u].send(ev)
                        except Exception:
                            pass
            return self._json(200, {"ok": True})

        if path == "/api/room/create":
            name = (b.get("name") or "").strip()
            user = b.get("user", "")
            if not db._conn().execute("SELECT 1 FROM users WHERE username=? AND is_admin=1", (user,)).fetchone():
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            if not name:
                return self._json(400, {"ok": False, "msg": "房间名不能为空"})
            rid = "room_" + str(int(time.time()))
            with rooms_lock:
                rooms[rid] = name
            broadcast({"t": "rooms", "rooms": rooms_list()})
            return self._json(200, {"ok": True, "id": rid})

        if path == "/api/room/join":
            user, rid = b.get("user", ""), b.get("room", "")
            with rooms_lock:
                if rid not in rooms:
                    return self._json(400, {"ok": False, "msg": "房间不存在"})
            with clients_lock:
                if user in clients:
                    clients[user].rooms.add(rid)
            return self._json(200, {"ok": True})

        if path == "/api/nickname":
            user, nick = b.get("user", ""), (b.get("nickname") or "").strip()
            if db.set_nickname(user, nick):
                with clients_lock:
                    if user in clients:
                        clients[user].nickname = nick
                broadcast({"t": "online", "online": online_list()})
                return self._json(200, {"ok": True})
            return self._json(400, {"ok": False, "msg": "失败"})

        if path == "/api/admin/ban":
            op, target = b.get("user", ""), b.get("target", "")
            ban = b.get("ban", True)
            if not db._conn().execute("SELECT 1 FROM users WHERE username=? AND is_admin=1", (op,)).fetchone():
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            if db.ban(target, ban):
                with clients_lock:
                    if target in clients and ban:
                        clients.pop(target, None)
                broadcast({"t": "online", "online": online_list()})
                return self._json(200, {"ok": True, "msg": ("封禁" if ban else "解封") + "成功"})
            return self._json(400, {"ok": False, "msg": "操作失败"})

        self._json(404, {"ok": False, "msg": "not found"})


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    ip = get_lan_ip()
    print("=" * 48)
    print("  Termux 聊天室已启动 (Render 版)")
    print("  端口:   %d" % PORT)
    print("  数据:   %s" % DB_FILE)
    print("  后台:   /admin.html  (admin / admin123)")
    print("=" * 48)
    httpd = socketserver.ThreadingTCPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()
