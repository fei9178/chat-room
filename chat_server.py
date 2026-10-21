#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Termux 聊天室后端 (单文件)
================================
功能: 账号密码注册/登录、群聊房间、私聊、在线列表、消息持久化(CSV)、管理后台
依赖: 仅标准库 (Python 3.7+)，Termux 自带
运行: python3 chat_server.py
浏览器访问: http://手机局域网IP:8000  (局域网内手机/电脑都能访问)
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
import csv
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# ============ 配置 ============
HOST = "0.0.0.0"
PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")          # 前端文件目录
DB_DIR = os.path.join(BASE_DIR, "data")          # 数据目录
USERS_FILE = os.path.join(DB_DIR, "users.csv")
MSG_FILE = os.path.join(DB_DIR, "messages.csv")
ADMIN_USER = "admin"                             # 初始管理员账号
ADMIN_PASS = "admin123"                          # 初始管理员密码(首次登录后建议改)

os.makedirs(WEB_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

# ============ 简易线程安全数据库 ============
class DB:
    """用 CSV 做持久化, 内存做热缓存, 适合几十人小聊天室"""

    def __init__(self):
        self.lock = threading.Lock()
        self._init_files()
        self.users = {}          # username -> {password_hash, nickname, is_admin, banned}
        self.load_users()

    def _init_files(self):
        if not os.path.exists(USERS_FILE):
            with open(USERS_FILE, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(["username", "password_hash", "nickname", "is_admin", "banned"])
        if not os.path.exists(MSG_FILE):
            with open(MSG_FILE, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(["time", "type", "from", "to", "content"])

    def load_users(self):
        with self.lock:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    self.users[row["username"]] = {
                        "password_hash": row["password_hash"],
                        "nickname": row.get("nickname", row["username"]),
                        "is_admin": row.get("is_admin", "0") == "1",
                        "banned": row.get("banned", "0") == "1",
                    }
            # 首次运行写入默认管理员
            if ADMIN_USER not in self.users:
                self.users[ADMIN_USER] = {
                    "password_hash": hashlib.sha256(ADMIN_PASS.encode()).hexdigest(),
                    "nickname": "管理员",
                    "is_admin": True,
                    "banned": False,
                }
                self._save_users()

    def _save_users(self):
        with open(USERS_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["username", "password_hash", "nickname", "is_admin", "banned"])
            for u, d in self.users.items():
                w.writerow([u, d["password_hash"], d["nickname"], "1" if d["is_admin"] else "0", "1" if d["banned"] else "0"])

    @staticmethod
    def hash_pwd(pwd):
        return hashlib.sha256(pwd.encode()).hexdigest()

    def register(self, username, password, nickname=""):
        username = username.strip().lower()
        if not re.match(r"^[a-zA-Z0-9_]{3,20}$", username):
            return False, "账号只能含字母/数字/下划线, 长度3-20"
        if len(password) < 4:
            return False, "密码至少4位"
        with self.lock:
            if username in self.users:
                return False, "账号已存在"
            self.users[username] = {
                "password_hash": self.hash_pwd(password),
                "nickname": nickname or username,
                "is_admin": False,
                "banned": False,
            }
            self._save_users()
        return True, "注册成功"

    def login(self, username, password):
        username = username.strip().lower()
        with self.lock:
            d = self.users.get(username)
            if not d:
                return False, "账号不存在", None
            if d["banned"]:
                return False, "该账号已被封禁", None
            if d["password_hash"] != self.hash_pwd(password):
                return False, "密码错误", None
            return True, "登录成功", {"username": username, "nickname": d["nickname"], "is_admin": d["is_admin"]}

    def list_users(self):
        with self.lock:
            return [{"username": u, "nickname": d["nickname"],
                     "is_admin": d["is_admin"], "banned": d["banned"]}
                    for u, d in self.users.items()]

    def ban(self, username, ban=True):
        username = username.strip().lower()
        with self.lock:
            if username in self.users and not self.users[username]["is_admin"]:
                self.users[username]["banned"] = ban
                self._save_users()
                return True
        return False

    def set_nickname(self, username, nickname):
        with self.lock:
            if username in self.users:
                self.users[username]["nickname"] = nickname[:20]
                self._save_users()
                return True
        return False

    def save_msg(self, mtype, frm, to, content):
        with self.lock:
            with open(MSG_FILE, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([datetime.now().isoformat(timespec="seconds"), mtype, frm, to, content])

    def history(self, limit=200):
        rows = []
        with self.lock:
            with open(MSG_FILE, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        return rows[-limit:]

db = DB()

# ============ 在线连接 & 消息分发 ============
class Client:
    def __init__(self, username, send):
        self.username = username
        self.nickname = db.users[username]["nickname"]
        self.send = send        # 发送函数: send(dict)
        self.rooms = set(["public"])

clients_lock = threading.Lock()
clients = {}        # username -> Client
rooms = {"public": "公共大厅"}   # room -> 名称
rooms_lock = threading.Lock()

def broadcast(event, room=None, skip=None):
    """向指定房间(或全部)推送事件"""
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
sse_lock = threading.Lock()

def handle_sse(username, wfile):
    """每个登录用户一条长连接, 用 SSE 推送消息"""
    client = Client(username, None)
    q = []          # 该用户的消息队列
    q_lock = threading.Lock()
    new_data = threading.Event()

    def send(ev):
        with q_lock:
            q.append(ev)
        new_data.set()

    client.send = send
    with clients_lock:
        old = clients.pop(username, None)   # 同账号后登录挤掉前者
        clients[username] = client

    # 发送历史消息(只发公共+该用户私聊)
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
    server_version = "TermuxChat/1.0"

    def log_message(self, *a):
        pass  # 安静一点

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

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _send_file(self, path):
        ext = os.path.splitext(path)[1]
        ctype = {"html": "text/html", "js": "application/javascript",
                 "css": "text/css", "png": "image/png", "svg": "image/svg+xml",
                 "json": "application/json", "ico": "image/x-icon"}.get(ext[1:], "application/octet-stream")
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

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

        # 前端静态资源兜底
        fpath = os.path.join(WEB_DIR, path.lstrip("/"))
        if os.path.isfile(fpath):
            return self._send_file(fpath)
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._read_json()
        if u.path.startswith("/api/"):
            return self._api_post(u.path, body)
        self.send_error(404)

    # ---- API ----
    def _api_get(self, path, qs):
        if path == "/api/stream":
            user = (qs.get("user") or [""])[0]
            if not user or user not in db.users:
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
            if not db.users.get(user, {}).get("is_admin"):
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
            if not user or user not in db.users:
                return self._json(403, {"ok": False, "msg": "请先登录"})
            if db.users[user]["banned"]:
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
            if to not in db.users:
                return self._json(400, {"ok": False, "msg": "对方不存在"})
            db.save_msg("pm", user, to, text)
            ev = {"t": "msg", "mtype": "pm", "user": user, "text": text,
                  "time": datetime.now().isoformat(timespec="seconds")}
            with clients_lock:
                if to in clients:
                    try: clients[to].send(ev)
                    except Exception: pass
                if user in clients:
                    try: clients[user].send(ev)
                    except Exception: pass
            return self._json(200, {"ok": True})

        if path == "/api/room/create":
            name = (b.get("name") or "").strip()
            user = b.get("user", "")
            if not db.users.get(user, {}).get("is_admin"):
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
            if not db.users.get(op, {}).get("is_admin"):
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
    print("  Termux 聊天室已启动")
    print("  本机:   http://localhost:%d" % PORT)
    print("  局域网: http://%s:%d" % (ip, PORT))
    print("  后台:   http://%s:%d/admin.html  (admin / admin123)" % (ip, PORT))
    print("  数据:   %s" % DB_DIR)
    print("  Ctrl+C 停止")
    print("=" * 48)
    httpd = socketserver.ThreadingTCPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()
