# -*- coding: utf-8 -*-
"""聊天室 - 单文件版
新增/修复: 加好友、房间密码、私信优化、管理员后台无响应修复
"""
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

NAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")
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
            c.execute("""CREATE TABLE IF NOT EXISTS friends(
                a TEXT NOT NULL, b TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                since TEXT NOT NULL,
                PRIMARY KEY (a, b))""")
            c.execute('CREATE INDEX IF NOT EXISTS idx_friends_b ON friends(b, status)')
            c.execute("""CREATE TABLE IF NOT EXISTS rooms(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL,
                password_hash TEXT, created TEXT NOT NULL)""")
            c.execute('CREATE INDEX IF NOT EXISTS idx_rooms_owner ON rooms(owner)')
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
        if not NAME_RE.match(username):
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
        info = {"username": username, "nickname": row["nickname"],
                "is_admin": bool(row["is_admin"]),
                "banned": bool(row["banned"])}
        return True, "登录成功", info

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
            mid = c.lastrowid
            c.commit(); c.close()
        return mid

    def history(self, limit=300):
        c = self._conn()
        rows = c.execute('SELECT id,time,type,"from","to",content FROM messages '
                         "WHERE id>(SELECT MAX(id)-? FROM messages) ORDER BY id",
                         (limit,)).fetchall()
        c.close()
        return [dict(r) for r in rows]

    def pm_history(self, a, b, limit=100):
        a, b = a.strip().lower(), b.strip().lower()
        c = self._conn()
        rows = c.execute('SELECT time,"from",content FROM messages WHERE type="pm" '
                         'AND (("from"=? AND "to"=?) OR ("from"=? AND "to"=?)) '
                         "ORDER BY id DESC LIMIT ?",
                         (a, b, b, a, limit)).fetchall()
        c.close()
        return [{"time": r["time"], "from": r["from"], "text": r["content"]} for r in reversed(rows)]

    def get_user(self, username):
        username = (username or "").strip().lower()
        if not username: return None
        c = self._conn()
        row = c.execute("SELECT username,password_hash,nickname,is_admin,banned FROM users WHERE username=?",
                        (username,)).fetchone()
        c.close()
        if not row: return None
        return {"username": row["username"], "password_hash": row["password_hash"],
                "nickname": row["nickname"], "is_admin": bool(row["is_admin"]),
                "banned": bool(row["banned"])}

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

    # ---------- 好友 ----------
    def _norm(self, a, b):
        a, b = a.strip().lower(), b.strip().lower()
        return (a, b) if a < b else (b, a)

    def friend_add(self, who, target):
        who, target = who.strip().lower(), target.strip().lower()
        if not target: return False, "请输入账号"
        if who == target: return False, "不能加自己为好友"
        if not NAME_RE.match(target) or not self.get_user(target):
            return False, "该用户不存在"
        if self.friend_status(who, target) == "friend":
            return False, "你们已经是好友了"
        if self.friend_status(who, target) == "pending":
            return False, "请求已发送, 等待对方同意"
        with self._lock:
            c = self._conn()
            now = datetime.datetime.now().isoformat(timespec="seconds")
            c.execute("INSERT OR IGNORE INTO friends(a,b,status,since) VALUES(?,?,?,?)",
                      (who, target, "pending", now))
            c.commit(); c.close()
        return True, "好友请求已发送"

    def friend_accept(self, who, target):
        who, target = who.strip().lower(), target.strip().lower()
        a, b = self._norm(who, target)
        with self._lock:
            c = self._conn()
            row = c.execute("SELECT status FROM friends WHERE a=? AND b=?", (a, b)).fetchone()
            if not row or row["status"] != "pending":
                c.close(); return False, "没有待处理的好友请求"
            if (a == who and b == target) or (a == target and b == who):
                pass
            # 只有收到请求的一方(请求发起者的目标)能同意; 这里简化为任一方点同意都行
            c.execute("UPDATE friends SET status='friend' WHERE a=? AND b=?", (a, b))
            c.commit(); c.close()
        return True, "已同意, 你们现在是好友了"

    def friend_reject(self, who, target):
        who, target = who.strip().lower(), target.strip().lower()
        a, b = self._norm(who, target)
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM friends WHERE a=? AND b=? AND status='pending'", (a, b))
            c.commit(); c.close()
        return True, "已拒绝"

    def friend_remove(self, who, target):
        who, target = who.strip().lower(), target.strip().lower()
        a, b = self._norm(who, target)
        with self._lock:
            c = self._conn()
            c.execute("DELETE FROM friends WHERE a=? AND b=?", (a, b))
            c.commit(); c.close()
        return True, "已删除好友"

    def friend_status(self, who, target):
        """返回 None / 'pending' / 'friend'"""
        who, target = who.strip().lower(), target.strip().lower()
        a, b = self._norm(who, target)
        c = self._conn()
        row = c.execute("SELECT status FROM friends WHERE a=? AND b=?", (a, b)).fetchone()
        c.close()
        return row["status"] if row else None

    def friend_list(self, who):
        who = who.strip().lower()
        c = self._conn()
        rows = c.execute("SELECT a,b,status,since FROM friends WHERE a=? OR b=?", (who, who)).fetchall()
        c.close()
        out = []
        for r in rows:
            other = r["b"] if r["a"] == who else r["a"]
            u = self.get_user(other)
            if not u: continue
            out.append({"username": other, "nickname": u["nickname"], "status": r["status"],
                        "since": r["since"], "is_admin": u["is_admin"], "banned": u["banned"]})
        return out

    def friend_pending(self, who):
        """发给 who 的待同意请求 (即 who 是目标方)"""
        who = who.strip().lower()
        c = self._conn()
        rows = c.execute("SELECT a,since FROM friends WHERE b=? AND status='pending'", (who,)).fetchall()
        c.close()
        out = []
        for r in rows:
            u = self.get_user(r["a"])
            if not u: continue
            out.append({"username": r["a"], "nickname": u["nickname"], "since": r["since"]})
        return out

    # ---------- 房间 ----------
    def room_create(self, owner, name, password=""):
        name = (name or "").strip()
        if not name: return None, "房间名不能为空"
        if len(name) > 20: return None, "房间名不超过20字"
        rid = "room_" + str(int(time.time() * 1000))
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            conn.execute("INSERT INTO rooms(id,name,owner,password_hash,created) VALUES(?,?,?,?,?)",
                         (rid, name[:20], owner.strip().lower(),
                          self.hash_pwd(password) if password else None,
                          datetime.datetime.now().isoformat(timespec="seconds")))
        return {"id": rid, "name": name[:20],
                "has_password": bool(password)}, "创建成功"

    def room_list(self):
        c = self._conn()
        rows = c.execute("SELECT id,name,owner,password_hash,created FROM rooms ORDER BY created").fetchall()
        c.close()
        out = [{"id": "public", "name": "公共大厅", "owner": "", "has_password": False,
                "members": 0, "online": 0, "created": ""}]
        for r in rows:
            out.append({"id": r["id"], "name": r["name"], "owner": r["owner"],
                        "has_password": bool(r["password_hash"]), "members": 0,
                        "online": 0, "created": r["created"]})
        return out

    def room_get(self, rid):
        if rid == "public":
            return {"id": "public", "name": "公共大厅", "owner": "", "has_password": False}
        c = self._conn()
        row = c.execute("SELECT id,name,owner,password_hash FROM rooms WHERE id=?", (rid,)).fetchone()
        c.close()
        if not row: return None
        return {"id": row["id"], "name": row["name"], "owner": row["owner"],
                "has_password": bool(row["password_hash"])}

    def room_check_pwd(self, rid, password):
        if rid == "public": return True
        if not password: return False
        c = self._conn()
        row = c.execute("SELECT password_hash FROM rooms WHERE id=?", (rid,)).fetchone()
        c.close()
        if not row or not row["password_hash"]: return True
        return row["password_hash"] == self.hash_pwd(password)

    def room_delete(self, rid):
        if rid == "public": return False, "公共大厅不可删除"
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            cur = conn.execute("DELETE FROM rooms WHERE id=?", (rid,))
            if cur.rowcount == 0: return False, "房间不存在"
        return True, "房间已解散"

    def search_users(self, keyword, exclude=None):
        keyword = (keyword or "").strip().lower()
        if not keyword: return []
        c = self._conn()
        rows = c.execute("SELECT username,nickname,is_admin FROM users WHERE username LIKE ? OR nickname LIKE ? LIMIT 20",
                         ("%" + keyword + "%", "%" + keyword + "%")).fetchall()
        c.close()
        out = []
        for r in rows:
            if exclude and r["username"] == exclude.strip().lower(): continue
            out.append({"username": r["username"], "nickname": r["nickname"],
                        "is_admin": bool(r["is_admin"])})
        return out


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


def online_set():
    with clients_lock:
        return set(clients.keys())


def rooms_list():
    rooms = db.room_list()
    with clients_lock:
        onmap = {}
        for c in clients.values():
            for r in c.rooms:
                onmap[r] = onmap.get(r, 0) + 1
        memmap = {}
        for c in clients.values():
            for r in c.rooms:
                if r == "public": continue
                memmap[r] = memmap.get(r, 0) + 1
    for r in rooms:
        r["online"] = onmap.get(r["id"], 0)
        if r["id"] != "public":
            r["members"] = max(r["members"], memmap.get(r["id"], 0))
    return rooms


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


# ==================================================================
#  内嵌前端
# ==================================================================
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
border-radius:10px;cursor:pointer;font-size:16px;flex-shrink:0;display:flex;align-items:center;justify-content:center;position:relative;}
.icon-btn:active{background:var(--line);}
.badge i{position:absolute;top:-2px;right:-2px;width:8px;height:8px;background:var(--warn);
border-radius:50%;display:none;}
.badge.on i{display:block;}
#room-tabs{display:flex;gap:8px;padding:10px 12px;overflow-x:auto;background:var(--panel);
border-bottom:1px solid var(--line);scrollbar-width:none;}
#room-tabs::-webkit-scrollbar{display:none;}
.room-tab{flex-shrink:0;padding:7px 14px;border-radius:20px;background:var(--panel2);
border:1px solid var(--line);color:var(--sub);font-size:13px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:5px;}
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
.msg.sys .bubble{background:transparent;border:1px dashed var(--line);color:var(--sub);text-align:center;font-size:12px;max-width:100%;}
.msg .who{font-size:11px;color:var(--sub);margin-bottom:3px;}
.msg .time{font-size:10px;color:var(--sub);opacity:.7;margin:0 4px;}
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
.panel{position:absolute;right:0;top:0;bottom:0;width:86%;max-width:340px;background:var(--panel);
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
.user-item .dot.pending{background:var(--warn);}
.user-item .un{flex:1;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;}
.user-item .nk{font-size:11px;color:var(--sub);}
.user-item .tag{font-size:10px;padding:2px 6px;border-radius:6px;background:var(--panel2);color:var(--sub);flex-shrink:0;}
.user-item .tag.admin{color:var(--accent);}
.user-item .tag.on{color:var(--ok);}
.user-item .tag.friend{color:var(--ok);}
.user-item .tag.pending{color:var(--warn);}
.sec-title{font-size:12px;color:var(--sub);padding:12px 8px 4px;font-weight:600;display:flex;justify-content:space-between;align-items:center;}
.sec-title .badge-new{background:var(--warn);color:#fff;font-size:10px;padding:1px 6px;border-radius:8px;}
.room-add{display:flex;gap:8px;margin-top:8px;}
.room-add input{flex:1;padding:9px 12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:14px;}
.room-add button{padding:9px 14px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;}
.btn-block{width:100%;padding:11px;border-radius:12px;border:1px solid var(--line);background:var(--panel2);
color:var(--txt);font-size:14px;cursor:pointer;margin-bottom:8px;text-align:left;}
.btn-block:active{background:var(--line);}
.btn-block.danger{color:var(--warn);border-color:rgba(248,81,73,.3);}
.btn-mini{padding:5px 10px;border:1px solid var(--line);background:var(--panel2);color:var(--txt);
border-radius:8px;font-size:12px;cursor:pointer;margin-left:6px;}
.btn-mini:active{opacity:.7;}
.btn-mini.ok{color:var(--ok);border-color:rgba(63,185,80,.3);}
.btn-mini.no{color:var(--warn);border-color:rgba(248,81,73,.3);}
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
.searchbox{display:flex;gap:8px;padding:8px 0;}
.searchbox input{flex:1;padding:10px 12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:14px;}
.searchbox button{padding:10px 16px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;}
.pwd-prompt{background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:16px;margin:10px;}
.pwd-prompt h4{font-size:15px;margin-bottom:8px;}
.pwd-prompt p{font-size:12px;color:var(--sub);margin-bottom:10px;}
.pwd-prompt input{width:100%;padding:10px 12px;background:var(--bg);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:14px;margin-bottom:10px;}
.pwd-prompt .row{display:flex;gap:8px;}
.pwd-prompt button{flex:1;padding:10px;border:none;border-radius:10px;font-size:14px;cursor:pointer;}
.pwd-prompt .ok{background:var(--accent);color:#fff;}
.pwd-prompt .no{background:var(--panel);color:var(--sub);border:1px solid var(--line);}
#toast{position:fixed;left:50%;transform:translateX(-50%);bottom:80px;padding:10px 18px;
background:rgba(0,0,0,.85);color:#fff;border-radius:22px;font-size:13px;z-index:999;transition:opacity .25s;}
#toast.err{background:var(--warn);}
#toast.ok{background:var(--ok);}
.unread-dot{position:absolute;top:-3px;right:-3px;min-width:16px;height:16px;padding:0 4px;
background:var(--warn);color:#fff;border-radius:8px;font-size:10px;display:none;align-items:center;justify-content:center;font-weight:600;}
</style>
</head>
<body>
<div id="screen-login">
<div class="auth">
<div class="logo">
<h1>💬 聊天室</h1>
<p>群聊 · 私聊 · 好友</p>
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
<div id="view-register">
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
<p class="hint">在线聊天室 · 云端存储<br>登录后可查看在线用户、添加好友</p>
</div>
</div>
<div id="screen-chat" class="hidden">
<header>
<div class="back">
<div class="title" id="cur-title">公共大厅</div>
<div class="sub" id="cur-sub">群聊</div>
</div>
<button class="icon-btn" id="btn-back" title="返回">←</button>
<button class="icon-btn badge" id="btn-users" title="在线用户">👥<i></i></button>
<button class="icon-btn" id="btn-friends" title="好友">👫<span class="unread-dot" id="friend-dot">0</span></button>
<button class="icon-btn" id="btn-menu" title="菜单">☰</button>
</header>
<div id="room-tabs"></div>
<div id="messages"></div>
<div id="composer">
<input id="input" placeholder="说点什么…" autocomplete="off">
<button id="send">➤</button>
</div>
</div>
<!-- 在线用户 -->
<div class="drawer" id="drawer-users">
<div class="mask" data-close="drawer-users"></div>
<div class="panel">
<div class="panel-h"><h3>在线用户</h3><button data-close="drawer-users">✕</button></div>
<div class="panel-b" id="user-list"></div>
</div>
</div>
<!-- 好友 -->
<div class="drawer" id="drawer-friends">
<div class="mask" data-close="drawer-friends"></div>
<div class="panel">
<div class="panel-h"><h3>好友</h3><button data-close="drawer-friends">✕</button></div>
<div class="panel-b" id="friend-list"></div>
</div>
</div>
<!-- 菜单 -->
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
<div class="sec-title">查找用户</div>
<div class="searchbox">
<input id="search-input" placeholder="输入账号或昵称">
<button id="btn-search">搜索</button>
</div>
<div id="search-result"></div>
<div class="sec-title">房间</div>
<div id="menu-rooms"></div>
<div class="room-add">
<input id="new-room" placeholder="新建房间">
<button id="btn-new-room">+</button>
</div>
<label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--sub);padding:10px 4px;">
<input type="checkbox" id="new-room-pwd"> 设置房间密码
</label>
<input id="new-room-pwdval" type="password" placeholder="房间密码 (留空则无密码)"
style="width:100%;padding:10px 12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:14px;display:none;">
<div class="sec-title">后台管理</div>
<button class="btn-block" id="btn-admin">👑 用户管理后台</button>
</div>
<div class="panel-f">
<button class="logout" id="btn-logout">退出登录</button>
</div>
</div>
</div>
<!-- 私聊 -->
<div class="drawer" id="drawer-pm">
<div class="mask" data-close="drawer-pm"></div>
<div class="panel">
<div class="panel-h">
<div style="display:flex;align-items:center;gap:8px;">
<div class="avatar" id="pm-avatar" style="width:32px;height:32px;font-size:13px;">?</div>
<h3 id="pm-title">私聊</h3>
</div>
<button id="pm-close" data-close="drawer-pm">✕</button>
</div>
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
.wrap{max-width:760px;margin:0 auto;}
h1{font-size:20px;margin-bottom:4px;}
.sub{color:var(--sub);font-size:13px;margin-bottom:16px;}
.login{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;max-width:360px;margin:60px auto;}
.login input{width:100%;padding:12px;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;color:var(--txt);font-size:15px;margin-bottom:12px;}
.login button{width:100%;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:15px;font-weight:600;}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;text-align:center;}
.stat .n{font-size:24px;font-weight:700;}
.stat .l{font-size:12px;color:var(--sub);margin-top:4px;}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden;margin-bottom:16px;}
.card h2{font-size:15px;padding:14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;}
table{width:100%;border-collapse:collapse;font-size:14px;}
th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line);}
th{color:var(--sub);font-size:12px;font-weight:600;cursor:pointer;user-select:none;}
th:hover{color:var(--accent);}
td.online{color:var(--ok);} td.offline{color:var(--sub);}
.mini{padding:5px 11px;border:1px solid var(--line);background:var(--panel2);color:var(--txt);
border-radius:8px;font-size:12px;cursor:pointer;margin-right:6px;}
.mini.danger{border-color:rgba(248,81,73,.3);color:var(--warn);}
.mini.ok{color:var(--ok);border-color:rgba(63,185,80,.3);}
.mini:active{opacity:.7;}
.hidden{display:none!important;}
.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px;}
.back{background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer;}
#toast{position:fixed;left:50%;transform:translateX(-50%);bottom:30px;padding:10px 18px;
background:rgba(0,0,0,.9);color:#fff;border-radius:22px;font-size:13px;z-index:999;}
#toast.ok{background:var(--ok);}
#toast.err{background:var(--warn);}
.pw-box{display:flex;gap:6px;}
.pw-box input{flex:1;padding:7px 10px;background:var(--panel2);border:1px solid var(--line);
border-radius:8px;color:var(--txt);font-size:13px;}
.empty{color:var(--sub);font-size:13px;padding:20px;text-align:center;}
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
<p class="sub" style="margin-top:14px;text-align:center;">默认 admin / admin123, 请尽快修改密码</p>
</div>
<div id="admin-box" class="hidden">
<div class="head">
<h1>🛡️ 聊天室管理后台</h1>
<div>
<button class="back" id="adm-back">← 返回聊天室</button>
<button class="back" id="adm-out" style="margin-left:12px;text-decoration:underline;">退出</button>
</div>
</div>
<p class="sub">用户/房间管理、封禁解封、重置密码、解散房间</p>
<div class="stats">
<div class="stat"><div class="n" id="st-users">-</div><div class="l">注册用户</div></div>
<div class="stat"><div class="n" id="st-online">-</div><div class="l">在线</div></div>
<div class="stat"><div class="n" id="st-banned">-</div><div class="l">已封禁</div></div>
<div class="stat"><div class="n" id="st-rooms">-</div><div class="l">房间数</div></div>
</div>
<div class="card">
<h2><span>房间管理</span><span style="font-size:12px;color:var(--sub);" id="room-count"></span></h2>
<div style="overflow-x:auto;">
<table id="room-table">
<thead><tr>
<th data-k="name">房间</th><th data-k="owner">创建者</th>
<th data-k="has_password">密码</th><th data-k="members">成员</th>
<th data-k="online">在线</th><th data-k="created">创建时间</th><th>操作</th>
</tr></thead>
<tbody id="room-tbody"></tbody>
</table>
</div>
</div>
<div class="card">
<h2><span>用户列表</span><span style="font-size:12px;color:var(--sub);" id="user-count"></span></h2>
<div class="pw-box" style="padding:12px 14px;border-bottom:1px solid var(--line);">
<input id="adm-search" placeholder="搜索账号或昵称…">
<button class="mini" id="adm-search-btn">搜索</button>
</div>
<div style="overflow-x:auto;">
<table id="user-table">
<thead><tr>
<th data-k="username">账号</th><th data-k="nickname">昵称</th>
<th data-k="banned">状态</th><th>重置密码</th><th>操作</th>
</tr></thead>
<tbody id="user-tbody"></tbody>
</table>
</div>
</div>
</div>
</div>
<script>
var admUser = localStorage.getItem("adm_user") || "";
var admPass = localStorage.getItem("adm_pass") || "";
var cache = {users:[], rooms:[], online:{}};
var sortState = {key:"username", asc:true};
function $(s){return document.querySelector(s);}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
function toast(m,type){var d=$("#toast");if(!d){d=document.createElement("div");d.id="toast";document.body.appendChild(d);}
d.className=type||"";d.textContent=m;setTimeout(function(){d.remove();},1800);}
function post(path,data,cb){fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)})
.then(function(r){return r.json();}).then(cb).catch(function(){cb({ok:false,msg:"网络错误"});});}
function checkAuth(cb){post("/api/login",{username:admUser,password:admPass},function(j){
if(j.ok&&j.user&&j.user.is_admin){cb(true);}else{localStorage.removeItem("adm_user");localStorage.removeItem("adm_pass");
admUser="";admPass="";cb(false);}});}
function tryStored(){if(!admUser||!admPass)return;
checkAuth(function(ok){if(ok){showAdmin();}else{$("#admin-box").classList.add("hidden");$("#login-box").classList.remove("hidden");}});}
function showAdmin(){$("#login-box").classList.add("hidden");$("#admin-box").classList.remove("hidden");load();}
$("#adm-go").onclick=function(){var u=$("#adm-user").value,p=$("#adm-pass").value;
post("/api/login",{username:u,password:p},function(j){if(j.ok&&j.user&&j.user.is_admin){
admUser=u;admPass=p;localStorage.setItem("adm_user",u);localStorage.setItem("adm_pass",p);showAdmin();
}else if(j.ok){toast("该账号不是管理员","err");}else{toast(j.msg,"err");}});};
$("#adm-out").onclick=function(){localStorage.removeItem("adm_user");localStorage.removeItem("adm_pass");location.reload();};
$("#adm-back").onclick=function(){location.href="/";};
function load(){checkAuth(function(ok){if(!ok){$("#login-box").classList.remove("hidden");$("#admin-box").classList.add("hidden");return;}loadData();});}
function loadData(){fetch("/api/admin/users?user="+encodeURIComponent(admUser)).then(function(r){return r.json();}).then(function(j){
if(!j.ok){if(j.msg&&j.msg.indexOf("权限")>=0){location.reload();}return;}
cache.users=j.users;cache.online={};j.online.forEach(function(o){cache.online[o.username]=true;});
$("#st-users").textContent=j.users.length;
$("#st-online").textContent=j.online.length;
$("#st-banned").textContent=j.users.filter(function(u){return u.banned;}).length;
renderUsers();});
fetch("/api/admin/rooms?user="+encodeURIComponent(admUser)).then(function(r){return r.json();}).then(function(j){
if(!j.ok)return;cache.rooms=j.rooms;
$("#st-rooms").textContent=j.rooms.length;
$("#room-count").textContent=j.rooms.length+" 个房间";
renderRooms();});}
function renderUsers(){var kw=($("#adm-search").value||"").toLowerCase(),rows=cache.users.filter(function(u){
return !kw || u.username.indexOf(kw)>=0 || (u.nickname||"").toLowerCase().indexOf(kw)>=0;});
var k=sortState.key,asc=sortState.asc;rows.sort(function(a,b){var x=a[k],y=b[k];
if(k==="banned"){x=a.banned?"1":"0";y=b.banned?"1":"0";}
return (x>y?1:x<y?-1:0)*(asc?1:-1);});
$("#user-count").textContent=rows.length+" 人";
var tb=$("#user-tbody");tb.innerHTML="";
rows.forEach(function(u){var on=!!cache.online[u.username];var tr=document.createElement("tr");
tr.innerHTML="<td>@"+esc(u.username)+(u.is_admin?' <small style="color:var(--accent)">[管理]</small>':"")+"</td>"+
"<td>"+esc(u.nickname)+"</td>"+
'<td class="'+(on?"online":"offline")+'">'+(on?"🟢 在线":"⚪ 离线")+(u.banned?" · 已封禁":"")+"</td>"+
"<td>"+(u.is_admin?'<span style="color:var(--sub);font-size:12px">—</span>':'<button class="mini ok" data-rpw="'+esc(u.username)+'">重置</button>')+"</td>"+
"<td>"+(u.is_admin?'<span style="color:var(--sub);font-size:12px">—</span>':
'<button class="mini '+(u.banned?"":"danger")+'" data-ban="'+esc(u.username)+'" data-val="'+(!u.banned)+'">'+(u.banned?"解封":"封禁")+"</button>")+"</td>";
tb.appendChild(tr);});}
function renderRooms(){var rows=cache.rooms.slice();var k=sortState.key,asc=sortState.asc;
rows.sort(function(a,b){var x=a[k],y=b[k];return (x>y?1:x<y?-1:0)*(asc?1:-1);});
var tb=$("#room-tbody");tb.innerHTML="";
rows.forEach(function(r){var tr=document.createElement("tr");
tr.innerHTML="<td>"+esc(r.name)+(r.id==="public"?' <small style="color:var(--sub)">[默认]</small>':"")+"</td>"+
"<td>"+(r.owner?"@"+esc(r.owner):"—")+"</td>"+
"<td>"+(r.has_password?'🔒 有':'—')+"</td>"+
"<td>"+r.members+"</td><td>"+r.online+"</td>"+
"<td style=\"font-size:12px;color:var(--sub);\">"+esc(r.created||"—")+"</td>"+
"<td>"+(r.id==="public"?'<span style="color:var(--sub);font-size:12px">—</span>':
'<button class="mini danger" data-del="'+esc(r.id)+'">解散</button>')+"</td>";
tb.appendChild(tr);});}
document.querySelectorAll("#user-table th[data-k],#room-table th[data-k]").forEach(function(th){
th.onclick=function(){var k=th.getAttribute("data-k");if(sortState.key===k){sortState.asc=!sortState.asc;}else{sortState.key=k;sortState.asc=true;}
renderUsers();renderRooms();};});
$("#adm-search-btn").onclick=renderUsers;
$("#adm-search").addEventListener("keydown",function(e){if(e.key==="Enter")renderUsers();});
document.addEventListener("click",function(e){var b=e.target;
if(b.classList&&b.classList.contains("mini")&&b.getAttribute("data-ban")){
var t=b.getAttribute("data-ban"),v=b.getAttribute("data-val")==="true";
post("/api/admin/ban",{user:admUser,target:t,ban:v},function(j){if(j.ok){toast(j.msg,"ok");loadData();}else{toast(j.msg,"err");}});}
if(b.classList&&b.classList.contains("mini")&&b.getAttribute("data-del")){
var rid=b.getAttribute("data-del");
if(!confirm("确定解散房间？消息记录将保留"))return;
post("/api/admin/room/delete",{user:admUser,room:rid},function(j){if(j.ok){toast("已解散","ok");loadData();}else{toast(j.msg,"err");}});}
if(b.classList&&b.classList.contains("mini")&&b.getAttribute("data-rpw")){
var t=b.getAttribute("data-rpw"),nv=prompt("为 @"+t+" 设置新密码 (至少4位):","");
if(!nv)return;if(nv.length<4){toast("密码至少4位","err");return;}
post("/api/admin/password",{user:admUser,target:t,new_password:nv},function(j){if(j.ok){toast("已重置为: "+nv,"ok");}else{toast(j.msg,"err");}});}
});
tryStored();
setInterval(function(){if($("#admin-box").classList.contains("hidden"))return;loadData();},8000);
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
curRoom: "public", view: "room",
rooms: [], evSource: null, online: [],
};
var friends = {list:[], pending:[], map:{}, unread:{}};
var ui = {};
function $(s){return document.querySelector(s);}
function el(html){var t=document.createElement("template");t.innerHTML=html.trim();return t.content.firstChild;}
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;","&gt;","&quot;","&#39;"}[c];});}
function iniOf(n){return (n&&n.charAt(0).toUpperCase())||"?";}
function toast(m,type){var o=$("#toast");if(o)o.remove();
var d=el('<div id="toast" class="'+(type||"")+'">'+esc(m)+"</div>");document.body.appendChild(d);
setTimeout(function(){d.style.opacity="0";},1500);setTimeout(function(){d.remove();},1800);}
function post(path,data,cb){fetch(API+path,{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify(data||{})});then(function(r){return r.json();})
.then(function(j){cb&&cb(j);}).catch(function(){cb&&cb({ok:false,msg:"网络错误"});});}
function get(path,cb){fetch(API+path).then(function(r){return r.json();}).then(function(j){cb&&cb(j);})
.catch(function(){cb&&cb({ok:false,msg:"网络错误"});});}

/* ---------- 登录页标签 ---------- */
function showAuthMsg(m,cls){var d=$("#auth-msg");d.textContent=m;d.className="msg-line"+(cls?" "+cls:"");}
function tab(name){["login","register","admin"].forEach(function(k){
$("#tab-"+k).classList.toggle("on",k===name);
$("#view-"+k).classList.toggle("hidden",k!==name);});showAuthMsg("");}
$("#tab-login").onclick=function(){tab("login");};
$("#tab-register").onclick=function(){tab("register");};
$("#tab-admin").onclick=function(){tab("admin");};
$("#btn-login").onclick=function(){var u=$("#login-user").value,p=$("#login-pass").value;
if(!u||!p)return showAuthMsg("请填写账号和密码","err");
post("/api/login",{username:u,password:p},function(j){if(j.ok)enter(j.user);else showAuthMsg(j.msg,"err");});};
$("#btn-register").onclick=function(){var u=$("#reg-user").value,n=$("#reg-nick").value,p=$("#reg-pass").value;
if(!u||!p)return showAuthMsg("请填写账号和密码","err");
post("/api/register",{username:u,password:p,nickname:n},function(j){if(j.ok){
showAuthMsg("注册成功, 正在登录…","ok");
post("/api/login",{username:u,password:p},function(k){if(k.ok)enter(k.user);else showAuthMsg(k.msg,"err");});
}else showAuthMsg(j.msg,"err");});};
$("#btn-admlogin").onclick=function(){var u=$("#adm-user").value,p=$("#adm-pass").value;
post("/api/login",{username:u,password:p},function(j){if(j.ok&&j.user&&j.user.is_admin)location.href="/admin";
else if(j.ok)showAuthMsg("该账号不是管理员, 已拒绝","err");else showAuthMsg(j.msg,"err");});};

/* ---------- 进入聊天 ---------- */
function enter(user){state.user=user.username;state.nickname=user.nickname;state.isAdmin=user.is_admin;
localStorage.setItem("chat_user",user.username);
localStorage.setItem("chat_pass",$("#login-pass").value||$("#reg-pass").value);
$("#me-avatar").textContent=iniOf(user.nickname);
$("#me-nick").textContent=user.nickname;
$("#me-user").textContent="@"+user.username+(user.is_admin?" · 管理员":"");
$("#screen-login").classList.add("hidden");
$("#screen-chat").classList.remove("hidden");
openStream();loadRooms();loadFriends();}
function tryAutoLogin(){var u=localStorage.getItem("chat_user"),p=localStorage.getItem("chat_pass");
if(u&&p)post("/api/login",{username:u,password:p},function(j){if(j.ok)enter(j.user);});}
$("#btn-logout").onclick=function(){localStorage.removeItem("chat_pass");
if(state.evSource)state.evSource.close();location.reload();};

/* ---------- SSE ---------- */
function openStream(){if(state.evSource)state.evSource.close();
state.evSource=new EventSource("/api/stream?user="+encodeURIComponent(state.user));
state.evSource.onmessage=function(e){try{handleEvent(JSON.parse(e.data));}catch(err){}};
state.evSource.onerror=function(){setTimeout(openStream,3000);};}
function handleEvent(ev){switch(ev.t){
case "hello":state.rooms=ev.rooms;renderRoomTabs();renderOnline(ev.online||[]);break;
case "rooms":state.rooms=ev.rooms;renderRoomTabs();break;
case "online":renderOnline(ev.online||[]);break;
case "friends":renderFriends(ev);break;
case "msg":if(ev.mtype==="group"){handleGroup(ev);}else if(ev.mtype==="pm"){handlePm(ev);}break;}}

/* ---------- 房间 ---------- */
function loadRooms(){get("/api/rooms",function(j){if(j.ok){state.rooms=j.rooms;renderRoomTabs();}});}
function renderRoomTabs(){var box=$("#room-tabs");box.innerHTML="";
state.rooms.forEach(function(r){var b=el('<button class="room-tab'+(r.id===state.curRoom?" on":"")+'">'
+(r.has_password?'🔒 ':"")+esc(r.name)+"</button>");
b.onclick=function(){switchRoom(r.id);};box.appendChild(b);});
var mb=$("#menu-rooms");mb.innerHTML="";
state.rooms.forEach(function(r){var btn=el('<button class="btn-block">'+(r.has_password?'🔒 ':"")+esc(r.name)+"</button>");
btn.onclick=function(){joinRoom(r.id);closeDrawer("drawer-menu");};mb.appendChild(btn);});}
function switchRoom(id){state.curRoom=id;state.view="room";
var r=state.rooms.filter(function(x){return x.id===id;})[0]||{id:id,name:"公共大厅"};
$("#cur-title").textContent=r.name;
$("#cur-sub").textContent=(r.has_password?"🔒 加密房间 · ":"")+(r.online?r.online+" 人在此 · ":"")+id;
Array.prototype.forEach.call(document.querySelectorAll("#room-tabs .room-tab"),function(b,i){
b.classList.toggle("on",state.rooms[i]&&state.rooms[i].id===id);});
closeDrawer("drawer-pm");
$("#messages").innerHTML="";loadHistory(id);}
function joinRoom(rid){var r=state.rooms.filter(function(x){return x.id===rid;})[0];
if(r&&r.has_password){askRoomPassword(rid,function(ok){if(ok)doJoin(rid);});}else{doJoin(rid);}}
function askRoomPassword(rid,cb){var box=$("#messages");
var old=box.innerHTML;box.innerHTML="";
var card=el('<div class="pwd-prompt"><h4>🔒 房间已加密</h4><p>输入密码以加入</p>'
+'<input type="password" id="rp-input" placeholder="房间密码"><div class="row">'
+'<button class="ok" id="rp-ok">进入</button><button class="no" id="rp-cancel">取消</button></div></div>');
box.appendChild(card);
$("#rp-input").focus();
$("#rp-ok").onclick=function(){var v=$("#rp-input").value;
post("/api/room/join",{user:state.user,room:rid,password:v},function(j){
if(j.ok){cb(true);}else{toast(j.msg,"err");cb(false);}});};
$("#rp-cancel").onclick=function(){cb(false);switchRoom("public");};
$("#rp-input").addEventListener("keydown",function(e){if(e.key==="Enter")$("#rp-ok").click();});}
function doJoin(rid){post("/api/room/join",{user:state.user,room:rid},function(j){
if(!j.ok)return toast(j.msg,"err");
if(state.rooms.filter(function(x){return x.id===rid;}).length===0)loadRooms();
switchRoom(rid);});}
function loadHistory(room){get("/api/history?room="+encodeURIComponent(room),function(j){
if(!j.ok)return;$("#messages").innerHTML="";
j.messages.forEach(function(m){appendGroup({user:m.from,text:m.content,time:m.time},true);});scrollBottom();});}

/* ---------- 群消息 ---------- */
function handleGroup(ev){appendGroup(ev,ev.room===state.curRoom);
if(ev.room===state.curRoom)scrollBottom();}
function appendGroup(m,visible){if(!visible)return;
var mine=m.user===state.user;var who=mine?"我":m.user;
var d=el('<div class="msg'+(mine?" me":"")+'">'+'<div class="avatar">'+iniOf(who)+"</div>"+
'<div><div class="who">'+esc(who)+'<span class="time">'+esc(m.time||"")+"</span></div>"+
'<div class="bubble">'+esc(m.text)+"</div></div></div>");
$("#messages").appendChild(d);}
function scrollBottom(){var m=$("#messages");m.scrollTop=m.scrollHeight;}
function send(){var v=$("#input").value.trim();if(!v)return;
post("/api/send",{user:state.user,room:state.curRoom,text:v},function(j){if(!j.ok)toast(j.msg,"err");});
$("#input").value="";}
$("#send").onclick=send;
$("#input").addEventListener("keydown",function(e){if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send();}});

/* ---------- 在线用户 ---------- */
function renderOnline(list){state.online=list;var box=$("#user-list");box.innerHTML="";
if(!list.length)box.innerHTML='<div class="empty">当前没有人在线</div>';
list.forEach(function(u){var it=el('<div class="user-item" data-u="'+esc(u.username)+'">'
+'<span class="dot"></span>'+'<div style="flex:1;min-width:0;"><div class="un">'+esc(u.nickname)
+' <small style="color:var(--sub)">@'+esc(u.username)+"</small></div>"
+'<div class="nk">'+(u.rooms||[]).map(esc).join(" · ")+"</div></div>"
+'<button class="btn-mini" data-act="pm">私聊</button>'
+'<button class="btn-mini ok" data-act="friend">加好友</button></div>');
if(u.username!==state.user){it.onclick=function(e){
var act=(e.target.getAttribute&&e.target.getAttribute("data-act"));
if(act==="pm")openPm(u.username,u.nickname);
else if(act==="friend")addFriend(u.username);};}
box.appendChild(it);});
$("#btn-users").classList.toggle("on",list.length>0);}

/* ---------- 好友 ---------- */
function loadFriends(){get("/api/friends?user="+encodeURIComponent(state.user),function(j){
if(!j.ok)return;friends.list=j.friends||[];friends.pending=j.pending||[];friends.map={};
friends.list.forEach(function(f){friends.map[f.username]=f.status;});
renderFriendDot();});}
function renderFriendDot(){var n=friends.pending.length;
$("#friend-dot").style.display=n>0?"flex":"none";
$("#friend-dot").textContent=n>9?"9+":n;}
function renderFriends(ev){if(ev&&ev.pending)renderFriendList(ev.pending);}
function renderFriendList(pending){var box=$("#friend-list");box.innerHTML="";
if(pending&&pending.length){box.innerHTML='<div class="sec-title">新的请求 <span class="badge-new">'+pending.length+"</span></div>";
pending.forEach(function(u){var it=el('<div class="user-item"><span class="dot pending"></span>'
+'<div style="flex:1;"><div class="un">'+esc(u.nickname)+' <small style="color:var(--sub)">@'+esc(u.username)+"</small></div></div>"
+'<button class="btn-mini ok" data-act="acc" data-u="'+esc(u.username)+'">同意</button>'
+'<button class="btn-mini no" data-act="rej" data-u="'+esc(u.username)+'">拒绝</button></div>');
box.appendChild(it);});}
box.innerHTML+='<div class="sec-title">我的好友 <span style="color:var(--sub);font-size:11px;">'+friends.list.length+"</span></div>";
var fl=friends.list.filter(function(f){return f.status==="friend";});
if(!fl.length)box.innerHTML+='<div class="empty">还没有好友, 去在线列表或搜索添加吧</div>';
fl.forEach(function(f){var it=el('<div class="user-item" data-u="'+esc(f.username)+'">'
+'<span class="dot"></span>'+'<div style="flex:1;"><div class="un">'+esc(f.nickname)
+' <small style="color:var(--sub)">@'+esc(f.username)+"</small></div>"
+'<div class="nk">'+(friends.map[f.username]==="friend"?"好友":"请求中")+"</div></div>"
+'<button class="btn-mini" data-act="pm">私聊</button>'
+'<button class="btn-mini no" data-act="del">删除</button></div>');
it.onclick=function(e){var act=e.target.getAttribute&&e.target.getAttribute("data-act"),u=f.username;
if(act==="pm")openPm(u,f.nickname);
else if(act==="del"){if(confirm("删除好友 @"+u+"?"))delFriend(u);}
else if(act==="acc")acceptFriend(u);
else if(act==="rej")rejectFriend(u);};
box.appendChild(it);});}
function openFriendPanel(){loadFriends();renderFriendList(friends.pending);openDrawer("drawer-friends");}
function addFriend(u){post("/api/friend/add",{user:state.user,target:u},function(j){toast(j.msg,j.ok?"ok":"err");loadFriends();});}
function acceptFriend(u){post("/api/friend/accept",{user:state.user,target:u},function(j){toast(j.msg,j.ok?"ok":"err");loadFriends();});}
function rejectFriend(u){post("/api/friend/reject",{user:state.user,target:u},function(j){toast(j.msg,j.ok?"ok":"err");loadFriends();});}
function delFriend(u){post("/api/friend/remove",{user:state.user,target:u},function(j){toast(j.msg,j.ok?"ok":"err");loadFriends();});}

/* ---------- 私信 (优化) ---------- */
var pmState={to:null,toNick:"",};
function openPm(uname,nick){pmState.to=uname;pmState.toNick=nick||uname;
$("#pm-title").textContent=nick||uname;$("#pm-avatar").textContent=iniOf(nick||uname);
$("#pm-messages").innerHTML='<div class="msg sys"><div class="bubble">正在加载…</div></div>';
closeDrawer("drawer-users");closeDrawer("drawer-friends");openDrawer("drawer-pm");
loadPmHistory(uname);}
function loadPmHistory(u){get("/api/pm/history?user="+encodeURIComponent(state.user)+"&with="+encodeURIComponent(u),
function(j){$("#pm-messages").innerHTML="";
if(!j.ok||!j.messages)return;
j.messages.forEach(function(m){appendPm(m.from===state.user,false,{text:m.text,time:m.time});});
scrollPm();});}
function handlePm(ev){var me=ev.user===state.user;
if($("#drawer-pm").classList.contains("on")&&pmState.to&&pmState.to===ev.user){
appendPm(me,false,ev);scrollPm();}
else{toast("新私信: "+ev.text.slice(0,24));}}
function appendPm(mine,sys,m){if(sys){var d=el('<div class="msg sys"><div class="bubble">'+esc(m.text)+"</div></div>");
$("#pm-messages").appendChild(d);return;}
var who=mine?"我":(pmState.toNick||m.user);
var d=el('<div class="msg'+(mine?" me":"")+" pm\">"+'<div class="avatar">'+iniOf(who)+"</div>"+
'<div><div class="who">'+esc(who)+'<span class="time">'+esc(m.time||"")+"</span></div>"+
'<div class="bubble">'+esc(m.text)+"</div></div></div>");
$("#pm-messages").appendChild(d);}
function scrollPm(){var p=$("#pm-messages");p.scrollTop=p.scrollHeight;}
function sendPm(){var v=$("#pm-input").value.trim();if(!v||!pmState.to)return;
post("/api/pm",{user:state.user,to:pmState.to,text:v});
appendPm(true,false,{text:v,time:new Date().toISOString().slice(0,16).replace("T"," ")});
$("#pm-input").value="";scrollPm();}
$("#pm-send").onclick=sendPm;
$("#pm-input").addEventListener("keydown",function(e){if(e.key==="Enter"){e.preventDefault();sendPm();}});

/* ---------- 抽屉 ---------- */
function openDrawer(id){$("#"+id).classList.add("on");}
function closeDrawer(id){$("#"+id).classList.remove("on");}
document.addEventListener("click",function(e){var t=e.target,c=t.getAttribute&&t.getAttribute("data-close");
if(c)closeDrawer(c);
if(t.classList&&t.classList.contains("user-item")){var u=t.getAttribute("data-u");
if(u&&u!==state.user&&!t.querySelector("[data-act]"))openPm(u,$(".un",t).textContent.trim());}
if(t.classList&&t.classList.contains("btn-mini")&&t.getAttribute("data-act")){
var act=t.getAttribute("data-act"),u=t.getAttribute("data-u");
if(act==="pm")openPm(u,$(".un",t).textContent.trim());
else if(act==="friend")addFriend(u);
else if(act==="acc")acceptFriend(u);
else if(act==="rej")rejectFriend(u);
else if(act==="del")delFriend(u);}});
function $(sel,root){return (root||document).querySelector(sel);}
$("#btn-users").onclick=function(){openDrawer("drawer-users");};
$("#btn-friends").onclick=function(){openFriendPanel();};
$("#btn-menu").onclick=function(){openDrawer("drawer-menu");};
$("#btn-back").onclick=function(){if($("#drawer-pm").classList.contains("on")){closeDrawer("drawer-pm");}
else if(state.view!=="room"){state.view="room";}else{switchRoom("public");}};
$("#btn-admin").onclick=function(){closeDrawer("drawer-menu");
if(state.isAdmin)window.open("/admin","_blank");else toast("需要管理员权限","err");};

/* ---------- 个人设置 ---------- */
$("#btn-nick").onclick=function(){var n=$("#nick-input").value.trim();
if(!n)return toast("昵称不能为空","err");
post("/api/nickname",{user:state.user,nickname:n},function(j){if(j.ok){state.nickname=n;
$("#me-nick").textContent=n;toast("昵称已更新","ok");}else toast(j.msg,"err");});};
$("#btn-pass").onclick=function(){var o=$("#pass-old").value,nw=$("#pass-new").value;
if(!o||!nw)return toast("请填写完整","err");if(nw.length<4)return toast("新密码至少4位","err");
post("/api/password",{user:state.user,old_password:o,new_password:nw},function(j){if(j.ok){
$("#pass-old").value="";$("#pass-new").value="";toast("密码已修改, 请重新登录","ok");
setTimeout(function(){location.reload();},1200);}else toast(j.msg,"err");});};

/* ---------- 搜索 / 建房间 ---------- */
$("#btn-search").onclick=function(){var k=$("#search-input").value.trim();
if(!k)return;get("/api/users/search?user="+encodeURIComponent(state.user)+"&q="+encodeURIComponent(k),
function(j){$("#search-result").innerHTML="";
if(!j.ok||!j.users||!j.users.length){$("#search-result").innerHTML='<div class="empty">没有找到相关用户</div>';return;}
j.users.forEach(function(u){if(u.username===state.user)return;
var st=friends.map[u.username];var tag=st==="friend"?'<span class="tag friend">好友</span>'
:st==="pending"?'<span class="tag pending">请求中</span>':"";
var btn=st==="friend"?'<button class="btn-mini" data-act="pm">私聊</button>'
:st==="pending"?'<span class="tag pending">请求中</span>':'<button class="btn-mini ok" data-act="add">加好友</button>';
var it=el('<div class="user-item"><span class="dot off"></span>'
+'<div style="flex:1;"><div class="un">'+esc(u.nickname)+' <small style="color:var(--sub)">@'+esc(u.username)+"</small></div></div>"
+tag+btn+"</div>");
it.onclick=function(e){var a=e.target.getAttribute&&e.target.getAttribute("data-act");
if(a==="add")addFriend(u.username);else openPm(u.username,u.nickname);};
$("#search-result").appendChild(it);});});};
$("#search-input").addEventListener("keydown",function(e){if(e.key==="Enter")$("#btn-search").click();});
$("#new-room-pwd").onchange=function(){$("#new-room-pwdval").style.display=this.checked?"block":"none";};
$("#btn-new-room").onclick=function(){var n=$("#new-room").value.trim(),pwd=$("#new-room-pwdval").value;
if(!n)return;post("/api/room/create",{user:state.user,name:n,password:pwd},function(j){
if(j.ok){$("#new-room").value="";$("#new-room-pwdval").value="";$("#new-room-pwd").checked=false;
$("#new-room-pwdval").style.display="none";toast("房间已创建","ok");loadRooms();switchRoom(j.id);}
else toast(j.msg,"err");});};
tryAutoLogin();
})();
''')


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ChatRoom/2.0"
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

    # ---------- GET 接口 ----------
    def _require_admin(self, user):
        u = db.get_user(user or "")
        if not u or not u["is_admin"]:
            return False
        return True

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

        if path == "/api/pm/history":
            user = (qs.get("user") or [""])[0]
            who = (qs.get("with") or [""])[0]
            if not user or not who or not db.get_user(user) or not db.get_user(who):
                return self._json(400, {"ok": False, "msg": "参数错误"})
            return self._json(200, {"ok": True, "messages": db.pm_history(user, who)})

        if path == "/api/users/search":
            user = (qs.get("user") or [""])[0]
            q = (qs.get("q") or [""])[0]
            if not db.get_user(user):
                return self._json(403, {"ok": False, "msg": "请先登录"})
            return self._json(200, {"ok": True, "users": db.search_users(q, user)})

        if path == "/api/friends":
            user = (qs.get("user") or [""])[0]
            if not db.get_user(user):
                return self._json(403, {"ok": False, "msg": "请先登录"})
            return self._json(200, {"ok": True,
                                    "friends": db.friend_list(user),
                                    "pending": db.friend_pending(user)})

        if path == "/api/admin/users":
            user = (qs.get("user") or [""])[0]
            if not self._require_admin(user):
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            return self._json(200, {"ok": True, "users": db.list_users(), "online": online_list()})

        if path == "/api/admin/rooms":
            user = (qs.get("user") or [""])[0]
            if not self._require_admin(user):
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            return self._json(200, {"ok": True, "rooms": rooms_list()})

        self._json(404, {"ok": False, "msg": "not found"})

    # ---------- POST 接口 ----------
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
            if not db.get_user(user) or not db.get_user(to):
                return self._json(400, {"ok": False, "msg": "对方不存在"})
            if db.get_user(user)["banned"]:
                return self._json(403, {"ok": False, "msg": "账号已被封禁"})
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
            user, name, pwd = b.get("user", ""), (b.get("name") or "").strip(), b.get("password", "")
            if not db.get_user(user):
                return self._json(403, {"ok": False, "msg": "请先登录"})
            if db.get_user(user)["banned"]:
                return self._json(403, {"ok": False, "msg": "账号已被封禁"})
            info, msg = db.room_create(user, name, pwd)
            if not info:
                return self._json(400, {"ok": False, "msg": msg})
            broadcast({"t": "rooms", "rooms": rooms_list()})
            return self._json(200, {"ok": True, "id": info["id"], "name": info["name"],
                                    "has_password": info["has_password"], "msg": msg})

        if path == "/api/room/join":
            user, rid, pwd = b.get("user", ""), b.get("room", ""), b.get("password", "")
            if not db.get_user(user):
                return self._json(403, {"ok": False, "msg": "请先登录"})
            room = db.room_get(rid)
            if not room:
                return self._json(400, {"ok": False, "msg": "房间不存在"})
            if room["has_password"] and not db.room_check_pwd(rid, pwd):
                return self._json(401, {"ok": False, "msg": "房间密码错误"})
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

        # ---------- 好友 ----------
        if path == "/api/friend/add":
            ok, msg = db.friend_add(b.get("user", ""), b.get("target", ""))
            return self._json(200 if ok else 400, {"ok": ok, "msg": msg})

        if path == "/api/friend/accept":
            ok, msg = db.friend_accept(b.get("user", ""), b.get("target", ""))
            if ok:
                broadcast({"t": "friends", "pending": db.friend_pending(b.get("user", ""))})
            return self._json(200 if ok else 400, {"ok": ok, "msg": msg})

        if path == "/api/friend/reject":
            ok, msg = db.friend_reject(b.get("user", ""), b.get("target", ""))
            return self._json(200, {"ok": ok, "msg": msg})

        if path == "/api/friend/remove":
            ok, msg = db.friend_remove(b.get("user", ""), b.get("target", ""))
            return self._json(200, {"ok": ok, "msg": msg})

        # ---------- 管理员 ----------
        if path == "/api/admin/ban":
            op, target, ban = b.get("user", ""), b.get("target", ""), b.get("ban", True)
            if not self._require_admin(op):
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            if target.strip().lower() == op.strip().lower():
                return self._json(400, {"ok": False, "msg": "不能对自己操作"})
            if db.ban(target, ban):
                with clients_lock:
                    if target in clients and ban:
                        clients.pop(target, None)
                broadcast({"t": "online", "online": online_list()})
                return self._json(200, {"ok": True, "msg": ("封禁" if ban else "解封") + "成功"})
            return self._json(400, {"ok": False, "msg": "操作失败"})

        if path == "/api/admin/password":
            op, target, new = b.get("user", ""), b.get("target", ""), b.get("new_password", "")
            if not self._require_admin(op):
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
            if not self._require_admin(op):
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            ok, msg = db.room_delete(rid)
            if not ok:
                return self._json(400, {"ok": False, "msg": msg})
            with clients_lock:
                for c in clients.values():
                    c.rooms.discard(rid)
            broadcast({"t": "rooms", "rooms": rooms_list()})
            return self._json(200, {"ok": True, "msg": msg})

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
