# -*- coding: utf-8 -*-
"""Termux 聊天室 - 单文件版"""
import os, sys, json, re, socket, sqlite3, hashlib, threading, time
import datetime
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")
DB_DIR = os.environ.get("DB_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, os.environ.get("DB_FILE", "chat.db").lstrip("/"))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")

import http.server, socketserver

rooms_lock = threading.Lock()
rooms = {"public": "公共大厅"}
room_members = {"public": []}
PWD_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")


class DB:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._lock:
            c = self._conn()
            c.execute("""CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                nickname TEXT NOT NULL DEFAULT '', is_admin INTEGER NOT NULL DEFAULT 0,
                banned INTEGER NOT NULL DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, type TEXT,
                "from" TEXT, "to" TEXT, content TEXT)""")
            c.execute('CREATE INDEX IF NOT EXISTS idx_msg_type_to ON messages(type, "to")')
            row = c.execute("SELECT 1 FROM users WHERE username=?", (ADMIN_USER,)).fetchone()
            if not row:
                c.execute("INSERT INTO users(username,password_hash,nickname,is_admin) VALUES(?,?,?,1)",
                          (ADMIN_USER, hashlib.sha256(ADMIN_PASS.encode()).hexdigest(), "管理员"))
            c.commit(); c.close()

    @staticmethod
    def hash_pwd(p):
        return hashlib.sha256(p.encode("utf-8")).hexdigest()

    def register(self, username, password, nickname=""):
        username = username.strip().lower()
        if not PWD_RE.match(username):
            return False, "账号只能含字母/数字/下划线, 长度3-20"
        if len(password) < 4:
            return False, "密码至少4位"
        with self._lock:
            c = self._conn()
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                c.close(); return False, "账号已存在"
            c.execute("INSERT INTO users(username,password_hash,nickname) VALUES(?,?,?)",
                      (username, self.hash_pwd(password), nickname or username))
            c.commit(); c.close()
        return True, "注册成功"

    def login(self, username, password):
        username = username.strip().lower()
        c = self._conn()
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        c.close()
        if not row: return False, "账号不存在", None
        if row["banned"]: return False, "该账号已被封禁", None
        if row["password_hash"] != self.hash_pwd(password): return False, "密码错误", None
        return True, "登录成功", {"username": username, "nickname": row["nickname"],
                                  "is_admin": bool(row["is_admin"])}

    def change_password(self, username, old_password, new_password):
        username = username.strip().lower()
        if len(new_password) < 4:
            return False, "新密码至少4位"
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
            if not row: c.close(); return False, "账号不存在"
            if row["password_hash"] != self.hash_pwd(old_password):
                c.close(); return False, "原密码错误"
            c.execute("UPDATE users SET password_hash=? WHERE username=?",
                      (self.hash_pwd(new_password), username))
            c.commit(); c.close()
        return True, "密码修改成功"

    def list_users(self):
        c = self._conn()
        rows = c.execute("SELECT username,nickname,is_admin,banned FROM users").fetchall()
        c.close()
        return [{"username": r["username"], "nickname": r["nickname"],
                 "is_admin": bool(r["is_admin"]), "banned": bool(r["banned"])} for r in rows]

    def ban(self, username, ban=True):
        username = username.strip().lower()
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT is_admin FROM users WHERE username=?", (username,)).fetchone()
            if row and not row["is_admin"]:
                c.execute("UPDATE users SET banned=? WHERE username=?", (1 if ban else 0, username))
                c.commit(); c.close(); return True
            c.close(); return False

    def set_nickname(self, username, nickname):
        with self._lock:
            c = self._conn()
            c.execute("UPDATE users SET nickname=? WHERE username=?", (nickname[:20], username.strip().lower()))
            ok = c.total_changes > 0
            c.commit(); c.close()
            return ok

    def save_msg(self, mtype, frm, to, content):
        with self._lock:
            c = self._conn()
            c.execute('INSERT INTO messages(time,type,"from","to",content) VALUES(?,?,?,?,?)',
                      (datetime.datetime.now().isoformat(timespec="seconds"), mtype, frm, to, content))
            c.commit(); c.close()

    def history(self, limit=300):
        c = self._conn()
        rows = c.execute('SELECT time,type,"from","to",content FROM messages '
                         "WHERE id>(SELECT MAX(id)-? FROM messages) ORDER BY id",
                         (limit,)).fetchall()
        c.close()
        return [dict(r) for r in rows]

    def get_user(self, username):
        username = (username or "").strip().lower()
        if not username: return None
        c = self._conn()
        row = c.execute("SELECT username,password_hash,nickname,is_admin,banned FROM users WHERE username=?",
                        (username,)).fetchone()
        c.close()
        if not row: return None
        return {"password_hash": row["password_hash"], "nickname": row["nickname"],
                "is_admin": bool(row["is_admin"]), "banned": bool(row["banned"])}

    @property
    def users(self):
        class _V:
            def __init__(self, db): self.db = db
            def get(self, k, default=None):
                r = self.db.get_user(k); return r if r is not None else default
            def __contains__(self, k): return self.db.get_user(k) is not None
            def __getitem__(self, k):
                r = self.db.get_user(k)
                if r is None: raise KeyError(k)
                return r
        return _V(self)

db = DB(DB_PATH)


class Client:
    def __init__(self, username, send):
        self.username = username
        self.nickname = db.get_user(username)["nickname"]
        self.send = send
        self.rooms = set(["public"])

clients_lock = threading.Lock()
clients = {}


def broadcast(event, room=None, skip=None):
    dead = []
    with clients_lock:
        if room is None:
            targets = list(clients.values())
        else:
            targets = [c for c in clients.values() if room in c.rooms]
        for c in targets:
            if skip is not None and c.username == skip:
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
        out = [{"id": k, "name": v, "owner": "", "members": len(room_members.get(k, [])),
                "online": 0} for k, v in rooms.items()]
    with clients_lock:
        onmap = {}
        for c in clients.values():
            for r in c.rooms:
                onmap[r] = onmap.get(r, 0) + 1
    for r in out:
        r["online"] = onmap.get(r["id"], 0)
    return out


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

_EMBED_INDEX = (
'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>聊天室</title>
<style>
:root{--bg:#0f1419;--panel:#161d26;--panel2:#1c2531;--line:#2a3444;
--txt:#e6ebf2;--sub:#8a97a8;--accent:#4f9cff;--accent2:#7b5cf0;
--me:#2b5fb8;--pm:#c084fc;--ok:#3fb950;--warn:#f85149;}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;}
html,body{height:100%;}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--txt);overflow:hidden;}
button,input{font-family:inherit;}
.hidden{display:none!important;}
#screen-login{height:100%;display:flex;align-items:center;justify-content:center;padding:20px;}
.auth{width:100%;max-width:380px;background:var(--panel);border:1px solid var(--line);
border-radius:20px;padding:26px 22px;box-shadow:0 20px 60px rgba(0,0,0,.5);}
.logo{text-align:center;margin-bottom:20px;}
.logo h1{font-size:26px;background:linear-gradient(90deg,var(--accent),var(--accent2));
-webkit-background-clip:text;background-clip:text;color:transparent;}
.logo p{color:var(--sub);font-size:13px;margin-top:6px;}
.tabs{display:flex;gap:6px;background:var(--panel2);padding:4px;border-radius:12px;margin-bottom:16px;}
.tabs button{flex:1;border:none;background:none;color:var(--sub);padding:9px;border-radius:9px;
font-size:14px;cursor:pointer;transition:.15s;}
.tabs button.on{background:var(--accent);color:#fff;}
.auth input{width:100%;padding:12px 14px;background:var(--panel2);border:1px solid var(--line);
border-radius:12px;color:var(--txt);font-size:15px;margin-bottom:10px;}
.auth input:focus{outline:none;border-color:var(--accent);}
.auth .submit{width:100%;padding:12px;border:none;border-radius:12px;font-size:15px;font-weight:600;
background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;cursor:pointer;margin-top:4px;}
.auth .submit:active{transform:scale(.98);}
.msg-line{text-align:center;font-size:13px;min-height:18px;margin-top:10px;}
.msg-line.err{color:var(--warn);}
.msg-line.ok{color:var(--ok);}
.hint{font-size:12px;color:var(--sub);text-align:center;margin-top:16px;line-height:1.6;}
.hint code{background:var(--panel2);padding:2px 6px;border-radius:5px;color:var(--accent);}
#screen-chat{height:100%;display:flex;flex-direction:column;}
header{display:flex;align-items:center;gap:10px;padding:12px 14px;background:var(--panel);
border-bottom:1px solid var(--line);}
header .back{flex:1;min-width:0;}
header .title{font-weight:600;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
header .sub{font-size:12px;color:var(--sub);}
.icon-btn{border:none;background:var(--panel2);color:var(--txt);width:38px;height:38px;
border-radius:10px;cursor:pointer;font-size:16px;flex-shrink:0;display:flex;align-items:center;justify-content:center;}
.icon-btn:active{background:var(--line);}
.badge{position:relative;}
.badge i{position:absolute;top:-2px;right:-2px;width:8px;height:8px;background:var(--warn);
border-radius:50%;display:none;}
.badge.on i{display:block;}
#room-tabs{display:flex;gap:8px;padding:10px 12px;overflow-x:auto;background:var(--panel);
border-bottom:1px solid var(--line);scrollbar-width:none;}
#room-tabs::-webkit-scrollbar{display:none;}
.room-tab{flex-shrink:0;padding:7px 14px;border-radius:20px;background:var(--panel2);
border:1px solid var(--line);color:var(--sub);font-size:13px;cursor:pointer;white-space:nowrap;}
.room-tab.on{background:var(--accent);color:#fff;border-color:var(--accent);}
#messages{flex:1;overflow-y:auto;padding:14px 12px;scroll-behavior:smooth;}
.msg{display:flex;margin-bottom:12px;gap:8px;align-items:flex-end;}
.msg.me{flex-direction:row-reverse;}
.avatar{width:32px;height:32px;border-radius:50%;background:var(--accent2);color:#fff;
display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex-shrink:0;}
.bubble{max-width:72%;padding:9px 13px;border-radius:14px;font-size:15px;line-height:1.5;
word-break:break-word;background:var(--panel2);}
.msg.me .bubble{background:var(--me);}
.msg.pm .bubble{border:1px solid var(--pm);}
.msg .who{font-size:11px;color:var(--sub);margin-bottom:3px;}
.msg .time{font-size:10px;color:var(--sub);opacity:.7;margin:0 4px;}
.sys{text-align:center;color:var(--sub);font-size:12px;margin:8px 0;}
#composer{display:flex;gap:8px;padding:10px 12px;background:var(--panel);
border-top:1px solid var(--line);align-items:center;}
#input{flex:1;padding:11px 14px;background:var(--panel2);border:1px solid var(--line);
border-radius:22px;color:var(--txt);font-size:15px;}
#input:focus{outline:none;border-color:var(--accent);}
#send{border:none;background:var(--accent);color:#fff;width:44px;height:44px;border-radius:50%;
cursor:pointer;font-size:18px;flex-shrink:0;}
#send:active{transform:scale(.92);}
.drawer{position:fixed;inset:0;z-index:50;display:none;}
.drawer.on{display:block;}
.mask{position:absolute;inset:0;background:rgba(0,0,0,.5);}
.panel{position:absolute;right:0;top:0;bottom:0;width:82%;max-width:320px;background:var(--panel);
display:flex;flex-direction:column;animation:slide .2s ease;}
@keyframes slide{from{transform:translateX(100%);}to{transform:translateX(0);}}
.panel-h{display:flex;align-items:center;justify-content:space-between;padding:14px;
border-bottom:1px solid var(--line);}
.panel-h h3{font-size:16px;}
.panel-h button{border:none;background:none;color:var(--sub);font-size:22px;cursor:pointer;}
.panel-b{flex:1;overflow-y:auto;padding:8px;}
.panel-f{padding:12px;border-top:1px solid var(--line);}
.user-item{display:flex;align-items:center;gap:10px;padding:10px;border-radius:12px;cursor:pointer;}
.user-item:active{background:var(--panel2);}
.user-item .dot{width:9px;height:9px;border-radius:50%;background:var(--ok);flex-shrink:0;}
.user-item .dot.off{background:var(--line);}
.user-item .uname{min-width:0;flex:1;}
.user-item .un{flex:1;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.user-item .tag{font-size:10px;padding:2px 6px;border-radius:6px;background:var(--panel2);color:var(--sub);}
.user-item .tag.admin{color:var(--accent);}
.user-item .tag.banned{color:var(--warn);}
.sec-title{font-size:12px;color:var(--sub);padding:12px 8px 4px;font-weight:600;}
.room-add{display:flex;gap:8px;margin-top:8px;}
.room-add input{flex:1;padding:9px 12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:14px;}
.room-add button{padding:9px 14px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;}
.btn-block{width:100%;padding:11px;border-radius:12px;border:1px solid var(--line);background:var(--panel2);
color:var(--txt);font-size:14px;cursor:pointer;margin-bottom:8px;text-align:left;}
.btn-block:active{background:var(--line);}
.btn-block.danger{color:var(--warn);border-color:rgba(248,81,73,.3);}
.nick-edit{display:flex;gap:8px;}
.nick-edit input{flex:1;padding:10px 12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:14px;}
.nick-edit button{padding:10px 16px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;}
.pass-edit{display:flex;flex-direction:column;gap:8px;}
.pass-edit input{padding:10px 12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:14px;}
.me-card{display:flex;align-items:center;gap:12px;padding:6px 0 14px;}
.me-card .avatar{width:46px;height:46px;font-size:16px;}
.me-card .info .n{font-weight:600;}
.me-card .info .u{font-size:12px;color:var(--sub);}
.logout{width:100%;padding:11px;border-radius:12px;border:1px solid var(--line);background:transparent;
color:var(--warn);font-size:14px;cursor:pointer;margin-top:10px;}
</style>
</head>
<body>
<div id="screen-login">
<div class="auth">
<div class="logo">
<h1>💬 聊天室</h1>
<p>多人在线群聊 & 私聊</p>
</div>
<div class="tabs">
<button id="tab-login" class="on">登录</button>
<button id="tab-register">注册</button>
<button id="tab-admin">管理员</button>
</div>
<div id="view-login">
<input id="login-user" placeholder="账号" autocomplete="off" autocapitalize="off">
<input id="login-pass" type="password" placeholder="密码">
<button class="submit" id="btn-login">进入聊天室</button>
</div>
<div id="view-register" class="hidden">
<input id="reg-user" placeholder="账号 (3-20位字母数字下划线)" autocomplete="off" autocapitalize="off">
<input id="reg-nick" placeholder="昵称 (可选)" autocomplete="off">
<input id="reg-pass" type="password" placeholder="密码 (至少4位)">
<button class="submit" id="btn-register">注册并登录</button>
</div>
<div id="view-admin" class="hidden">
<input id="adm-user" placeholder="管理员账号" value="admin">
<input id="adm-pass" type="password" placeholder="密码" value="admin123">
<button class="submit" id="btn-admlogin">进入管理后台</button>
</div>
<div class="msg-line" id="auth-msg"></div>
<p class="hint">在线聊天室 · 云端存储<br>登录后点右上角 👥 查看在线用户</p>
</div>
</div>
<div id="screen-chat" class="hidden">
<header>
<div class="back">
<div class="title" id="cur-title">公共大厅</div>
<div class="sub" id="cur-sub">群聊</div>
</div>
<button class="icon-btn badge" id="btn-users" title="在线用户">👥<i></i></button>
<button class="icon-btn" id="btn-menu" title="菜单">☰</button>
</header>
<div id="room-tabs"></div>
<div id="messages"></div>
<div id="composer">
<input id="input" placeholder="说点什么…" autocomplete="off">
<button id="send">➤</button>
</div>
</div>
<div class="drawer" id="drawer-users">
<div class="mask" data-close="drawer-users"></div>
<div class="panel">
<div class="panel-h"><h3>在线用户</h3><button data-close="drawer-users">✕</button></div>
<div class="panel-b" id="user-list"></div>
</div>
</div>
<div class="drawer" id="drawer-menu">
<div class="mask" data-close="drawer-menu"></div>
<div class="panel">
<div class="panel-h"><h3>菜单</h3><button data-close="drawer-menu">✕</button></div>
<div class="panel-b">
<div class="me-card">
<div class="avatar" id="me-avatar">?</div>
<div class="info">
<div class="n" id="me-nick"></div>
<div class="u" id="me-user"></div>
</div>
</div>
<div class="sec-title">个人</div>
<div class="nick-edit">
<input id="nick-input" placeholder="新昵称">
<button id="btn-nick">保存</button>
</div>
<div class="sec-title">安全</div>
<div class="pass-edit">
<input id="pass-old" type="password" placeholder="当前密码">
<input id="pass-new" type="password" placeholder="新密码 (至少4位)">
<button class="btn-block" id="btn-pass">修改密码</button>
</div>
<div class="sec-title">房间</div>
<div id="menu-rooms"></div>
<div class="room-add">
<input id="new-room" placeholder="新建房间">
<button id="btn-new-room">+</button>
</div>
<div class="sec-title">后台管理</div>
<button class="btn-block" id="btn-admin">👑 用户管理后台</button>
</div>
<div class="panel-f">
<button class="logout" id="btn-logout">退出登录</button>
</div>
</div>
</div>
<div class="drawer" id="drawer-pm">
<div class="mask" data-close="drawer-pm"></div>
<div class="panel">
<div class="panel-h"><h3 id="pm-title">私聊</h3><button data-close="drawer-pm">✕</button></div>
<div class="panel-b" id="pm-messages"></div>
<div class="panel-f" style="display:flex;gap:8px;">
<input id="pm-input" placeholder="发私信…" autocomplete="off"
style="flex:1;padding:10px 14px;background:var(--panel2);border:1px solid var(--line);border-radius:22px;color:var(--txt);font-size:14px;">
<button id="pm-send" style="border:none;background:var(--accent);color:#fff;width:42px;height:42px;border-radius:50%;cursor:pointer;">➤</button>
</div>
</div>
</div>
<script src="app.js"></script>
</body>
</html>

''')

_EMBED_ADMIN = (
'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>聊天室管理后台</title>
<style>
:root{--bg:#0f1419;--panel:#161d26;--panel2:#1c2531;--line:#2a3444;
--txt:#e6ebf2;--sub:#8a97a8;--accent:#4f9cff;--ok:#3fb950;--warn:#f85149;}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--txt);min-height:100vh;padding:16px;}
.wrap{max-width:720px;margin:0 auto;}
h1{font-size:20px;margin-bottom:4px;}
.sub{color:var(--sub);font-size:13px;margin-bottom:16px;}
.login{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;max-width:360px;margin:60px auto;}
.login input{width:100%;padding:12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:15px;margin-bottom:12px;}
.login button{width:100%;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:15px;font-weight:600;}
.stats{display:flex;gap:10px;margin-bottom:16px;}
.stat{flex:1;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;text-align:center;}
.stat .n{font-size:24px;font-weight:700;}
.stat .l{font-size:12px;color:var(--sub);margin-top:4px;}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden;margin-bottom:16px;}
.card h2{font-size:15px;padding:14px;border-bottom:1px solid var(--line);}
table{width:100%;border-collapse:collapse;font-size:14px;}
th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line);}
th{color:var(--sub);font-size:12px;font-weight:600;}
td.online{color:var(--ok);} td.offline{color:var(--sub);}
.mini{padding:5px 11px;border:1px solid var(--line);background:var(--panel2);color:var(--txt);
border-radius:8px;font-size:12px;cursor:pointer;margin-right:6px;}
.mini.danger{border-color:rgba(248,81,73,.3);color:var(--warn);}
.mini:active{opacity:.7;}
.hidden{display:none!important;}
.logout{background:none;border:none;color:var(--sub);font-size:13px;cursor:pointer;text-decoration:underline;}
.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;}
.back{background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer;}
</style>
</head>
<body>
<div class="wrap">
<div id="login-box" class="login">
<h1>管理后台</h1>
<p class="sub" style="margin-bottom:18px;">仅管理员可访问</p>
<input id="adm-user" placeholder="管理员账号" value="admin">
<input id="adm-pass" type="password" placeholder="密码" value="admin123">
<button id="adm-go">登录</button>
<p class="sub" style="margin-top:14px;text-align:center;">默认 admin / admin123, 请登录后尽快修改密码</p>
</div>
<div id="admin-box" class="hidden">
<div class="head">
<h1>🛡️ 聊天室管理后台</h1>
<button class="back" id="adm-back">← 返回聊天室</button>
<button class="logout" id="adm-out">退出登录</button>
</div>
<p class="sub">可管理房间、查看所有用户与在线状态, 封禁/解封账号</p>
<div class="stats">
<div class="stat"><div class="n" id="st-users">-</div><div class="l">注册用户</div></div>
<div class="stat"><div class="n" id="st-online">-</div><div class="l">在线</div></div>
<div class="stat"><div class="n" id="st-banned">-</div><div class="l">已封禁</div></div>
<div class="stat"><div class="n" id="st-rooms">-</div><div class="l">房间数</div></div>
</div>
<div class="card">
<h2>房间管理</h2>
<div style="overflow-x:auto;">
<table>
<thead><tr><th>房间</th><th>创建者</th><th>成员</th><th>在线</th><th>操作</th></tr></thead>
<tbody id="room-tbody"></tbody>
</table>
</div>
</div>
<div class="card">
<h2>用户列表</h2>
<div style="overflow-x:auto;">
<table>
<thead><tr><th>用户</th><th>昵称</th><th>状态</th><th>操作</th></tr></thead>
<tbody id="user-tbody"></tbody>
</table>
</div>
</div>
</div>
</div>
<script>
var admUser = localStorage.getItem("adm_user") || "";
var admPass = localStorage.getItem("adm_pass") || "";
function $(s){return document.querySelector(s);}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function post(path, data, cb){
fetch(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)})
.then(function(r){return r.json();}).then(cb).catch(function(){cb({ok:false,msg:"网络错误"});});
}
function tryStored(){
if(!admUser || !admPass) return;
post("/api/login", {username:admUser, password:admPass}, function(j){
if(j.ok && j.user && j.user.is_admin){
$("#login-box").classList.add("hidden");
$("#admin-box").classList.remove("hidden"); load();
} else { localStorage.removeItem("adm_user"); localStorage.removeItem("adm_pass"); admUser=""; admPass=""; }
});
}
tryStored();
$("#adm-go").onclick = function(){
var u = $("#adm-user").value, p = $("#adm-pass").value;
post("/api/login", {username:u, password:p}, function(j){
if(j.ok && j.user && j.user.is_admin){
admUser=u; admPass=p;
localStorage.setItem("adm_user", u); localStorage.setItem("adm_pass", p);
$("#login-box").classList.add("hidden");
$("#admin-box").classList.remove("hidden"); load();
} else if(j.ok){ alert("该账号不是管理员"); }
else { alert(j.msg); }
});
};
$("#adm-out").onclick = function(){
localStorage.removeItem("adm_user"); localStorage.removeItem("adm_pass");
location.reload();
};
$("#adm-back").onclick = function(){ location.href = "/"; };
function load(){
fetch("/api/admin/users?user=" + encodeURIComponent(admUser)).then(function(r){return r.json();}).then(function(j){
if(!j.ok){ alert(j.msg); return; }
var users = j.users, online = j.online;
var onSet = {}; online.forEach(function(o){onSet[o.username]=true;});
$("#st-users").textContent = users.length;
$("#st-online").textContent = online.length;
$("#st-banned").textContent = users.filter(function(u){return u.banned;}).length;
var tb = $("#user-tbody"); tb.innerHTML = "";
users.forEach(function(u){
var on = !!onSet[u.username];
var tr = document.createElement("tr");
tr.innerHTML = "<td>@" + esc(u.username) + (u.is_admin?' <small style="color:var(--accent)">[管理]</small>':"") + "</td>" +
"<td>" + esc(u.nickname) + "</td>" +
'<td class="' + (on?"online":"offline") + '">' + (on?"🟢 在线":"⚪ 离线") + "</td>" +
"<td>" + (u.is_admin? "<span style=\"color:var(--sub);font-size:12px\">—</span>"
: '<button class="mini ' + (u.banned?"":"danger") + '" data-u="' + esc(u.username) + '" data-ban="' + !u.banned + '">'
+ (u.banned?"解封":"封禁") + "</button>") + "</td>";
tb.appendChild(tr);
});
});
fetch("/api/admin/rooms?user=" + encodeURIComponent(admUser)).then(function(r){return r.json();}).then(function(j){
if(!j.ok){ return; }
var rooms = j.rooms;
$("#st-rooms").textContent = rooms.length;
var tb = $("#room-tbody"); tb.innerHTML = "";
rooms.forEach(function(r){
var tr = document.createElement("tr");
tr.innerHTML = "<td>" + esc(r.name) + (r.id==="public"?' <small style="color:var(--sub)">[默认]</small>':"") + "</td>" +
"<td>@" + esc(r.owner||"—") + "</td>" +
"<td>" + r.members + "</td>" +
"<td>" + r.online + "</td>" +
"<td>" + (r.id==="public"? "<span style=\"color:var(--sub);font-size:12px\">—</span>"
: '<button class="mini danger" data-room="' + esc(r.id) + '">解散</button>') + "</td>";
tb.appendChild(tr);
});
});
}
document.addEventListener("click", function(e){
var b = e.target;
if(b.classList && b.classList.contains("mini") && b.dataset.u){
var ban = b.dataset.ban === "true";
post("/api/admin/ban", {user:admUser, target:b.dataset.u, ban:ban}, function(j){
if(j.ok){ load(); } else { alert(j.msg); }
});
}
if(b.classList && b.classList.contains("mini") && b.dataset.room){
if(!confirm("确定解散房间「" + b.dataset.room + "」?")) return;
post("/api/admin/room/delete", {user:admUser, room:b.dataset.room}, function(j){
if(j.ok){ load(); } else { alert(j.msg); }
});
}
});
setInterval(function(){ if(!$("#admin-box").classList.contains("hidden")) load(); }, 5000);
</script>
</body>
</html>

''')

_EMBED_APPJS = (
'''
/* 聊天室 - 前端逻辑 */
(function () {
"use strict";
var API = "";
var state = {
user: null, nickname: "", isAdmin: false,
curRoom: "public", rooms: [], evSource: null,
};
function $(s) { return document.querySelector(s); }
function $$(s) { return document.querySelectorAll(s); }
function el(html) {
var t = document.createElement("template");
t.innerHTML = html.trim();
return t.content.firstChild;
}
function esc(s) {
return String(s).replace(/[&<>"']/g, function (c) {
return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
});
}
function iniOf(name) { return (name && name.charAt(0).toUpperCase()) || "?"; }
function toast(msg, type) {
var old = document.getElementById("toast");
if (old) old.remove();
var d = el('<div id="toast" class="' + (type || "") + '">' + esc(msg) + "</div>");
var st = d.style;
st.position = "fixed"; st.left = "50%"; st.bottom = "80px";
st.transform = "translateX(-50%)"; st.padding = "10px 18px";
st.background = "rgba(0,0,0,.85)"; st.color = "#fff";
st.borderRadius = "22px"; st.fontSize = "13px"; st.zIndex = "999";
st.transition = "opacity .25s";
document.body.appendChild(d);
setTimeout(function () { d.style.opacity = "0"; }, 1500);
setTimeout(function () { d.remove(); }, 1800);
}
function post(path, data, cb) {
fetch(API + path, {
method: "POST",
headers: { "Content-Type": "application/json" },
body: JSON.stringify(data || {}),
}).then(function (r) { return r.json(); })
.then(function (j) { cb && cb(j); })
.catch(function () { cb && cb({ ok: false, msg: "网络错误, 请检查后端是否运行" }); });
}
function showAuthMsg(m, cls) {
var d = $("#auth-msg");
d.textContent = m;
d.className = "msg-line" + (cls ? " " + cls : "");
}
$("#tab-login").onclick = function () {
$("#tab-login").classList.add("on");
$("#tab-register").classList.remove("on");
$("#tab-admin").classList.remove("on");
$("#view-login").classList.remove("hidden");
$("#view-register").classList.add("hidden");
$("#view-admin").classList.add("hidden");
showAuthMsg("");
};
$("#tab-register").onclick = function () {
$("#tab-register").classList.add("on");
$("#tab-login").classList.remove("on");
$("#tab-admin").classList.remove("on");
$("#view-register").classList.remove("hidden");
$("#view-login").classList.add("hidden");
$("#view-admin").classList.add("hidden");
showAuthMsg("");
};
$("#tab-admin").onclick = function () {
$("#tab-admin").classList.add("on");
$("#tab-login").classList.remove("on");
$("#tab-register").classList.remove("on");
$("#view-admin").classList.remove("hidden");
$("#view-login").classList.add("hidden");
$("#view-register").classList.add("hidden");
showAuthMsg("");
};
$("#btn-login").onclick = function () {
var u = $("#login-user").value, p = $("#login-pass").value;
if (!u || !p) return showAuthMsg("请填写账号和密码", "err");
post("/api/login", { username: u, password: p }, function (j) {
if (j.ok) { enter(j.user); }
else { showAuthMsg(j.msg, "err"); }
});
};
$("#btn-register").onclick = function () {
var u = $("#reg-user").value, n = $("#reg-nick").value, p = $("#reg-pass").value;
if (!u || !p) return showAuthMsg("请填写账号和密码", "err");
post("/api/register", { username: u, password: p, nickname: n }, function (j) {
if (j.ok) {
showAuthMsg("注册成功, 正在登录…", "ok");
post("/api/login", { username: u, password: p }, function (k) {
if (k.ok) enter(k.user); else showAuthMsg(k.msg, "err");
});
} else { showAuthMsg(j.msg, "err"); }
});
};
$("#btn-admlogin").onclick = function () {
var u = $("#adm-user").value, p = $("#adm-pass").value;
post("/api/login", { username: u, password: p }, function (j) {
if (j.ok && j.user && j.user.is_admin) { location.href = "/admin"; }
else if (j.ok) { showAuthMsg("该账号不是管理员, 已拒绝", "err"); }
else { showAuthMsg(j.msg, "err"); }
});
};
function enter(user) {
state.user = user.username;
state.nickname = user.nickname;
state.isAdmin = user.is_admin;
localStorage.setItem("chat_user", user.username);
localStorage.setItem("chat_pass", $("#login-pass").value || $("#reg-pass").value);
$("#me-avatar").textContent = iniOf(user.nickname);
$("#me-nick").textContent = user.nickname;
$("#me-user").textContent = "@" + user.username + (user.is_admin ? " · 管理员" : "");
$("#screen-login").classList.add("hidden");
$("#screen-chat").classList.remove("hidden");
openStream();
loadRooms();
}
function tryAutoLogin() {
var u = localStorage.getItem("chat_user"), p = localStorage.getItem("chat_pass");
if (u && p) {
post("/api/login", { username: u, password: p }, function (j) {
if (j.ok) enter(j.user);
});
}
}
$("#btn-logout").onclick = function () {
localStorage.removeItem("chat_pass");
if (state.evSource) state.evSource.close();
location.reload();
};
function openStream() {
if (state.evSource) state.evSource.close();
state.evSource = new EventSource("/api/stream?user=" + encodeURIComponent(state.user));
state.evSource.onmessage = function (e) {
try { handleEvent(JSON.parse(e.data)); } catch (err) {}
};
state.evSource.onerror = function () { setTimeout(openStream, 3000); };
}
function handleEvent(ev) {
switch (ev.t) {
case "hello":
state.rooms = ev.rooms;
renderRoomTabs();
switchRoom("public");
renderOnline(ev.online || []);
break;
case "rooms":
state.rooms = ev.rooms;
renderRoomTabs();
break;
case "online":
renderOnline(ev.online || []);
break;
case "msg":
if (ev.mtype === "group") {
appendMsg(ev, ev.room === state.curRoom);
if (ev.room === state.curRoom) scrollBottom();
} else if (ev.mtype === "pm") {
handlePm(ev);
}
break;
}
}
function loadRooms() {
fetch("/api/rooms").then(function (r) { return r.json(); }).then(function (j) {
if (j.ok) { state.rooms = j.rooms; renderRoomTabs(); }
});
}
function renderRoomTabs() {
var box = $("#room-tabs");
box.innerHTML = "";
state.rooms.forEach(function (r) {
var b = el('<button class="room-tab' + (r.id === state.curRoom ? " on" : "") + '">' + esc(r.name) + "</button>");
b.onclick = function () { switchRoom(r.id); };
box.appendChild(b);
});
var mb = $("#menu-rooms");
mb.innerHTML = "";
state.rooms.forEach(function (r) {
var btn = el('<button class="btn-block">' + esc(r.name) + "</button>");
btn.onclick = function () {
post("/api/room/join", { user: state.user, room: r.id });
switchRoom(r.id);
closeDrawer("drawer-menu");
};
mb.appendChild(btn);
});
}
function switchRoom(id) {
state.curRoom = id;
var r = state.rooms.filter(function (x) { return x.id === id; })[0]
|| { id: id, name: (id === "public" ? "公共大厅" : id.replace(/^room_/, "房间 ")) };
$("#cur-title").textContent = r.name;
$("#cur-sub").textContent = ((r.online ? r.online + " 人在此 · " : "") + id);
Array.prototype.forEach.call($$("#room-tabs .room-tab"), function (b, i) {
b.classList.toggle("on", state.rooms[i] && state.rooms[i].id === id);
});
$("#messages").innerHTML = "";
loadHistory(id);
}
function loadHistory(room) {
fetch("/api/history?room=" + encodeURIComponent(room))
.then(function (r) { return r.json(); })
.then(function (j) {
if (!j.ok) return;
$("#messages").innerHTML = "";
j.messages.forEach(function (m) {
appendMsg({ mtype: "group", user: m.from, room: m.to, text: m.content, time: m.time }, true);
});
scrollBottom();
});
}
function appendMsg(m, visible) {
if (!visible) return;
var mine = m.user === state.user;
var who = mine ? "我" : m.user;
var d = el(
'<div class="msg ' + (mine ? "me" : "") + (m.mtype === "pm" ? " pm" : "") + '">' +
'<div class="avatar">' + iniOf(who) + "</div>" +
'<div><div class="who">' + esc(who) + '<span class="time">' + esc(m.time || "") + "</span></div>" +
'<div class="bubble">' + esc(m.text) + "</div></div>" +
"</div>"
);
$("#messages").appendChild(d);
}
function scrollBottom() {
var m = $("#messages");
m.scrollTop = m.scrollHeight;
}
function send() {
var v = $("#input").value.trim();
if (!v) return;
post("/api/send", { user: state.user, room: state.curRoom, text: v });
$("#input").value = "";
}
$("#send").onclick = send;
$("#input").addEventListener("keydown", function (e) {
if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
function renderOnline(list) {
var box = $("#user-list");
box.innerHTML = "";
if (!list.length) {
box.innerHTML = '<div style="color:var(--sub);font-size:13px;padding:20px;text-align:center;">当前没有人在线</div>';
}
list.forEach(function (u) {
var it = el(
'<div class="user-item" data-u="' + esc(u.username) + '">' +
'<span class="dot"></span>' +
'<div class="uname"><div class="un">' + esc(u.nickname) +
' <small style="color:var(--sub)">@' + esc(u.username) + '</small></div>' +
'<div class="nk" style="font-size:11px;color:var(--sub)">' + (u.rooms||[]).map(esc).join(" · ") + "</div></div>" +
"</div>"
);
if (u.username !== state.user) {
it.onclick = function () { openPm(u.username, u.nickname); };
}
box.appendChild(it);
});
$("#btn-users").classList.toggle("on", list.length > 0);
}
var pmState = { to: null, toNick: "", log: [] };
function openPm(uname, nick) {
pmState.to = uname;
pmState.toNick = nick || uname;
pmState.log = [];
$("#pm-title").textContent = "私聊 · " + pmState.toNick;
$("#pm-messages").innerHTML = "";
closeDrawer("drawer-users");
openDrawer("drawer-pm");
}
function handlePm(ev) {
var me = ev.user === state.user;
var who = me ? ("我 → " + pmState.toNick) : ev.user;
var d = el(
'<div class="msg ' + (me ? "me" : "") + ' pm">' +
'<div class="avatar">' + iniOf(ev.user) + "</div>" +
'<div><div class="who">' + esc(who) + '<span class="time">' + esc(ev.time || "") + "</span></div>" +
'<div class="bubble">' + esc(ev.text) + "</div></div>" +
"</div>"
);
if ($("#drawer-pm").classList.contains("on") && pmState.to === (me ? pmState.to : ev.user)) {
$("#pm-messages").appendChild(d);
var p = $("#pm-messages"); p.scrollTop = p.scrollHeight;
} else {
toast("新私信: " + ev.text.slice(0, 20));
}
}
function sendPm() {
var v = $("#pm-input").value.trim();
if (!v || !pmState.to) return;
post("/api/pm", { user: state.user, to: pmState.to, text: v });
$("#pm-input").value = "";
}
$("#pm-send").onclick = sendPm;
$("#pm-input").addEventListener("keydown", function (e) {
if (e.key === "Enter") { e.preventDefault(); sendPm(); }
});
function openDrawer(id) { $("#" + id).classList.add("on"); }
function closeDrawer(id) { $("#" + id).classList.remove("on"); }
document.addEventListener("click", function (e) {
var c = e.target.getAttribute && e.target.getAttribute("data-close");
if (c) closeDrawer(c);
});
$("#btn-users").onclick = function () { openDrawer("drawer-users"); };
$("#btn-menu").onclick = function () { openDrawer("drawer-menu"); };
$("#btn-admin").onclick = function () {
closeDrawer("drawer-menu");
if (state.isAdmin) window.open("/admin", "_blank");
else toast("需要管理员权限", "err");
};
$("#btn-nick").onclick = function () {
var n = $("#nick-input").value.trim();
if (!n) return toast("昵称不能为空", "err");
post("/api/nickname", { user: state.user, nickname: n }, function (j) {
if (j.ok) { state.nickname = n; $("#me-nick").textContent = n; toast("昵称已更新", "ok"); }
});
};
$("#btn-pass").onclick = function () {
var old = $("#pass-old").value, nw = $("#pass-new").value;
if (!old || !nw) return toast("请填写完整", "err");
if (nw.length < 4) return toast("新密码至少4位", "err");
post("/api/password", { user: state.user, old_password: old, new_password: nw }, function (j) {
if (j.ok) { $("#pass-old").value = ""; $("#pass-new").value = ""; toast("密码已修改, 请重新登录", "ok");
setTimeout(function(){ location.reload(); }, 1200); }
else toast(j.msg, "err");
});
};
$("#btn-new-room").onclick = function () {
var n = $("#new-room").value.trim();
if (!n) return;
post("/api/room/create", { user: state.user, name: n }, function (j) {
if (j.ok) { $("#new-room").value = ""; toast("房间已创建", "ok"); loadRooms();
var target = j.id || j.room;
if (target) switchRoom(target);
else if (state.rooms.length) switchRoom(state.rooms[state.rooms.length-1].id);
}
else toast(j.msg, "err");
});
};
tryAutoLogin();
})();

''')


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "TermuxChat/1.0"
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

    def _embed(self, body, ctype):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path):
        ext = os.path.splitext(path)[1]
        ctype = {"html": "text/html", "js": "application/javascript", "css": "text/css",
                 "png": "image/png", "svg": "image/svg+xml", "json": "application/json",
                 "ico": "image/x-icon"}.get(ext[1:], "application/octet-stream")
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
            return self._embed(_EMBED_INDEX, "text/html")
        if path == "/admin" or path == "/admin.html":
            return self._embed(_EMBED_ADMIN, "text/html")
        if path == "/app.js":
            return self._embed(_EMBED_APPJS, "application/javascript")
        if path.startswith("/api/"):
            return self._api_get(path, qs)

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

    def _api_get(self, path, qs):
        if path == "/api/stream":
            user = (qs.get("user") or [""])[0]
            if not user or not db.get_user(user):
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
            if not db.get_user(user) or not db.get_user(user)["is_admin"]:
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            return self._json(200, {"ok": True, "users": db.list_users(), "online": online_list()})

        if path == "/api/admin/rooms":
            user = (qs.get("user") or [""])[0]
            if not db.get_user(user) or not db.get_user(user)["is_admin"]:
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            return self._json(200, {"ok": True, "rooms": rooms_list()})

        self._json(404, {"ok": False, "msg": "not found"})

    def _api_post(self, path, b):
        if path == "/api/register":
            ok, msg = db.register(b.get("username", ""), b.get("password", ""), b.get("nickname", ""))
            return self._json(200 if ok else 400, {"ok": ok, "msg": msg})

        if path == "/api/login":
            ok, msg, info = db.login(b.get("username", ""), b.get("password", ""))
            return self._json(200 if ok else 400, {"ok": ok, "msg": msg, "user": info or None})

        if path == "/api/password":
            user, old, new = b.get("user", ""), b.get("old_password", ""), b.get("new_password", "")
            ok, msg = db.change_password(user, old, new)
            return self._json(200 if ok else 400, {"ok": ok, "msg": msg})

        if path == "/api/send":
            user, text, room = b.get("user", ""), (b.get("text") or "").strip(), b.get("room", "public")
            if not user or not db.get_user(user):
                return self._json(403, {"ok": False, "msg": "请先登录"})
            if db.get_user(user)["banned"]:
                return self._json(403, {"ok": False, "msg": "账号已被封禁"})
            if not text:
                return self._json(400, {"ok": False, "msg": "消息为空"})
            if len(text) > 2000:
                return self._json(400, {"ok": False, "msg": "消息过长"})
            db.save_msg("group", user, room, text)
            broadcast({"t": "msg", "mtype": "group", "user": user, "room": room,
                       "text": text, "time": datetime.datetime.now().isoformat(timespec="seconds")})
            return self._json(200, {"ok": True})

        if path == "/api/pm":
            user, to, text = b.get("user", ""), b.get("to", ""), (b.get("text") or "").strip()
            if not user or not to or not text:
                return self._json(400, {"ok": False, "msg": "参数缺失"})
            if not db.get_user(to):
                return self._json(400, {"ok": False, "msg": "对方不存在"})
            db.save_msg("pm", user, to, text)
            ev = {"t": "msg", "mtype": "pm", "user": user, "text": text,
                  "time": datetime.datetime.now().isoformat(timespec="seconds")}
            with clients_lock:
                for u in (to, user):
                    if u in clients:
                        try: clients[u].send(ev)
                        except Exception: pass
            return self._json(200, {"ok": True})

        if path == "/api/room/create":
            user, name = b.get("user", ""), (b.get("name") or "").strip()
            if not db.get_user(user):
                return self._json(403, {"ok": False, "msg": "请先登录"})
            if db.get_user(user)["banned"]:
                return self._json(403, {"ok": False, "msg": "账号已被封禁"})
            if not name:
                return self._json(400, {"ok": False, "msg": "房间名不能为空"})
            rid = "room_" + str(int(time.time()))
            with rooms_lock:
                rooms[rid] = name
                room_members[rid] = []
            broadcast({"t": "rooms", "rooms": rooms_list()})
            return self._json(200, {"ok": True, "id": rid, "name": name})

        if path == "/api/room/join":
            user, rid = b.get("user", ""), b.get("room", "")
            if not db.get_user(user):
                return self._json(403, {"ok": False, "msg": "请先登录"})
            with rooms_lock:
                if rid not in rooms:
                    return self._json(400, {"ok": False, "msg": "房间不存在"})
            with clients_lock:
                if user in clients:
                    clients[user].rooms.add(rid)
            return self._json(200, {"ok": True})

        if path == "/api/nickname":
            user, nick = b.get("user", ""), (b.get("nickname") or "").strip()
            if not nick:
                return self._json(400, {"ok": False, "msg": "昵称不能为空"})
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
            if not db.get_user(op) or not db.get_user(op)["is_admin"]:
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            if db.ban(target, ban):
                with clients_lock:
                    if target in clients and ban:
                        clients.pop(target, None)
                broadcast({"t": "online", "online": online_list()})
                return self._json(200, {"ok": True, "msg": ("封禁" if ban else "解封") + "成功"})
            return self._json(400, {"ok": False, "msg": "操作失败"})

        if path == "/api/admin/password":
            op, target, new = b.get("user", ""), b.get("target", ""), b.get("new_password", "")
            if not db.get_user(op) or not db.get_user(op)["is_admin"]:
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            if len(new) < 4:
                return self._json(400, {"ok": False, "msg": "密码至少4位"})
            with db._lock:
                c = db._conn()
                c.execute("UPDATE users SET password_hash=? WHERE username=?",
                          (DB.hash_pwd(new), target.strip().lower()))
                c.commit(); c.close()
            return self._json(200, {"ok": True, "msg": "密码已重置"})

        if path == "/api/admin/room/delete":
            op, rid = b.get("user", ""), b.get("room", "")
            if not db.get_user(op) or not db.get_user(op)["is_admin"]:
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            if rid == "public":
                return self._json(400, {"ok": False, "msg": "公共大厅不可删除"})
            with rooms_lock:
                rooms.pop(rid, None)
                room_members.pop(rid, None)
            broadcast({"t": "rooms", "rooms": rooms_list()})
            return self._json(200, {"ok": True, "msg": "房间已解散"})

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
    print("  聊天室已启动")
    print("  本机:   http://localhost:%d" % PORT)
    print("  局域网: http://%s:%d" % (ip, PORT))
    print("  后台:   http://%s:%d/admin  (admin / admin123)" % (ip, PORT))
    print("  Ctrl+C 停止")
    print("=" * 48)
    httpd = socketserver.ThreadingTCPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()

