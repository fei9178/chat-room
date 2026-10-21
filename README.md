# Termux 聊天室 (HTML + Python 单文件后端)

纯标准库实现的局域网聊天室: 账号密码注册登录、群聊房间、私聊、在线列表、消息持久化,
管理员后台可查看/封禁用户。零依赖, Termux 直接跑, 手机和电脑连同一 WiFi 都能访问。

## 目录结构

```
chat_server.py     # 后端 (一个文件, 仅用 Python 标准库)
web/
├── index.html     # 聊天界面
├── app.js         # 前端逻辑
└── admin.html     # 管理员后台
data/              # 自动生成: 用户表 users.csv + 消息记录 messages.csv
```

## 在 Termux 里运行

```bash
# 1. 把这三个文件放到同一目录, 例如 ~/chat
#    chat_server.py 和 web/ 目录要在同一层

cd ~/chat

# 2. Python 3.7+ 即可, Termux 默认自带
python3 chat_server.py
```

启动后看到:

```
Termux 聊天室已启动
本机:   http://localhost:8000
局域网: http://192.168.x.x:8000
后台:   http://192.168.x.x:8000/admin.html  (admin / admin123)
```

> 第一次启动时, 前端 `web/` 目录里的三个文件会自动作为静态资源被访问,
> 无需额外配置。默认管理员 `admin / admin123`, 登录后请到后台修改。

## 让局域网其他设备访问

Termux 默认会拒绝外部连接, 需要放行端口:

```bash
# 放行 8000 端口 (仅当前会话有效, 重启后重设)
nft add rule inet filter input tcp dport 8000 accept
```

然后:
- **手机浏览器**: 访问 `http://<手机IP>:8000` (同一台手机用 `localhost:8000`)
- **电脑**: 连同一 WiFi, 访问 `http://<手机IP>:8000`
- **其他手机**: 同上, 连同一 WiFi 后访问

> 找不到手机 IP 时, 在 Termux 里跑 `ip addr show wlan0`,
> 或者启动后端时终端会直接打印出来。

## 后台管理

浏览器打开 `http://<IP>:8000/admin.html`, 用管理员账号登录, 可以:
- 查看所有注册用户、谁在线、谁被封
- 封禁 / 解封账号 (被封用户无法登录, 在线会被踢下线)
- 统计数字每 5 秒自动刷新

## 接口速查 (想自己写客户端可用)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/register` | 注册 `{username, password, nickname}` |
| POST | `/api/login` | 登录 `{username, password}` |
| GET  | `/api/stream?user=x` | SSE 长连接, 推送消息/在线/房间事件 |
| POST | `/api/send` | 发群聊 `{user, room, text}` |
| POST | `/api/pm` | 发私聊 `{user, to, text}` |
| GET  | `/api/rooms` | 房间列表 |
| POST | `/api/room/create` | 建房间 (仅管理员) `{user, name}` |
| POST | `/api/room/join` | 加入房间 `{user, room}` |
| GET  | `/api/online` | 当前在线 |
| GET  | `/api/history?room=x` | 房间历史消息 |
| POST | `/api/nickname` | 改昵称 `{user, nickname}` |
| GET  | `/api/admin/users?user=x` | 用户列表 (仅管理员) |
| POST | `/api/admin/ban` | 封禁/解封 `{user, target, ban}` |

## 常驻后台运行

想让后端在 Termux 关掉前台后继续跑:

```bash
# 用 nohup
nohup python3 chat_server.py > chat.log 2>&1 &

# 或 tmux / screen
pkg install tmux
tmux new -d -s chat 'python3 chat_server.py'

# 查看日志
tail -f chat.log

# 停止
pkill -f chat_server.py
```

## 数据备份

所有数据都在 `data/` 目录下, 直接复制这个文件夹即可备份:

```bash
tar czf chat_backup.tar.gz data/
```

删掉 `data/` 会重置所有用户和聊天记录, 默认管理员也会重新生成。

## 常见问题

**连不上**: 检查是否放行了 8000 端口; 确认手机和电脑在同一 WiFi;
换热点组网时部分热点禁止设备互访, 改用同一个路由器。

**端口被占用**: 改 `chat_server.py` 顶部的 `PORT = 8000`, 同时访问地址和放行命令里的端口一起改。

**想换端口/只本机访问**: 改 `HOST = "127.0.0.1"` 则只有手机本机能访问,
局域网其他设备就进不来了。

**消息太多**: `messages.csv` 会一直增长, 定期清空即可
(`pkill` 停服务 → 清空文件 → 重启), 清空后聊天记录归零但用户保留。
