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
import sqlite3
import csv
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# ============ 配置 ============
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")          # 前端文件目录
DB_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))  # 数据目录
USERS_FILE = os.path.join(DB_DIR, "users.csv")
MSG_FILE = os.path.join(DB_DIR, "messages.csv")
ADMIN_USER = "admin"                             # 初始管理员账号
# 前端资源 (内嵌, 部署时无需 web/ 目录)
_EMBED_INDEX = (
'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">\n<title>Termux 聊天室</title>\n<style>\n  :root{\n    --bg:#0f1419; --panel:#161d26; --panel2:#1c2531; --line:#2a3444;\n    --txt:#e6ebf2; --sub:#8a97a8; --accent:#4f9cff; --accent2:#7b5cf0;\n    --me:#2b5fb8; --pm:#c084fc; --ok:#3fb950; --warn:#f85149;\n  }\n  *{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;}\n  html,body{height:100%;}\n  body{\n    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;\n    background:var(--bg); color:var(--txt); overflow:hidden;\n  }\n  button,input{font-family:inherit;}\n  .hidden{display:none!important;}\n\n  /* ===== 登录 ===== */\n  #screen-login{height:100%;display:flex;align-items:center;justify-content:center;padding:20px;}\n  .auth{width:100%;max-width:380px;background:var(--panel);border:1px solid var(--line);\n    border-radius:20px;padding:26px 22px;box-shadow:0 20px 60px rgba(0,0,0,.5);}\n  .logo{text-align:center;margin-bottom:20px;}\n  .logo h1{font-size:26px;background:linear-gradient(90deg,var(--accent),var(--accent2));\n    -webkit-background-clip:text;background-clip:text;color:transparent;}\n  .logo p{color:var(--sub);font-size:13px;margin-top:6px;}\n  .tabs{display:flex;gap:6px;background:var(--panel2);padding:4px;border-radius:12px;margin-bottom:16px;}\n  .tabs button{flex:1;border:none;background:none;color:var(--sub);padding:9px;border-radius:9px;\n    font-size:14px;cursor:pointer;transition:.15s;}\n  .tabs button.on{background:var(--accent);color:#fff;}\n  .auth input{width:100%;padding:12px 14px;background:var(--panel2);border:1px solid var(--line);\n    border-radius:12px;color:var(--txt);font-size:15px;margin-bottom:10px;}\n  .auth input:focus{outline:none;border-color:var(--accent);}\n  .auth .submit{width:100%;padding:12px;border:none;border-radius:12px;font-size:15px;font-weight:600;\n    background:linear-gradient(90deg,var(--accent),var(--accent2));color:#fff;cursor:pointer;margin-top:4px;}\n  .auth .submit:active{transform:scale(.98);}\n  .msg-line{text-align:center;font-size:13px;min-height:18px;margin-top:10px;}\n  .msg-line.err{color:var(--warn);}\n  .msg-line.ok{color:var(--ok);}\n  .hint{font-size:12px;color:var(--sub);text-align:center;margin-top:16px;line-height:1.6;}\n  .hint code{background:var(--panel2);padding:2px 6px;border-radius:5px;color:var(--accent);}\n\n  /* ===== 聊天主界面 ===== */\n  #screen-chat{height:100%;display:flex;flex-direction:column;}\n  header{display:flex;align-items:center;gap:10px;padding:12px 14px;background:var(--panel);\n    border-bottom:1px solid var(--line);}\n  header .back{flex:1;min-width:0;}\n  header .title{font-weight:600;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}\n  header .sub{font-size:12px;color:var(--sub);}\n  .icon-btn{border:none;background:var(--panel2);color:var(--txt);width:38px;height:38px;\n    border-radius:10px;cursor:pointer;font-size:16px;flex-shrink:0;display:flex;align-items:center;justify-content:center;}\n  .icon-btn:active{background:var(--line);}\n  .badge{position:relative;}\n  .badge i{position:absolute;top:-2px;right:-2px;width:8px;height:8px;background:var(--warn);\n    border-radius:50%;display:none;}\n  .badge.on i{display:block;}\n\n  /* 房间标签 */\n  #room-tabs{display:flex;gap:8px;padding:10px 12px;overflow-x:auto;background:var(--panel);\n    border-bottom:1px solid var(--line);scrollbar-width:none;}\n  #room-tabs::-webkit-scrollbar{display:none;}\n  .room-tab{flex-shrink:0;padding:7px 14px;border-radius:20px;background:var(--panel2);\n    border:1px solid var(--line);color:var(--sub);font-size:13px;cursor:pointer;white-space:nowrap;}\n  .room-tab.on{background:var(--accent);color:#fff;border-color:var(--accent);}\n\n  /* 消息区 */\n  #messages{flex:1;overflow-y:auto;padding:14px 12px;scroll-behavior:smooth;}\n  .msg{display:flex;margin-bottom:12px;gap:8px;align-items:flex-end;}\n  .msg.me{flex-direction:row-reverse;}\n  .avatar{width:32px;height:32px;border-radius:50%;background:var(--accent2);color:#fff;\n    display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;flex-shrink:0;}\n  .bubble{max-width:72%;padding:9px 13px;border-radius:14px;font-size:15px;line-height:1.5;\n    word-break:break-word;background:var(--panel2);}\n  .msg.me .bubble{background:var(--me);}\n  .msg.pm .bubble{border:1px solid var(--pm);}\n  .msg .who{font-size:11px;color:var(--sub);margin-bottom:3px;}\n  .msg .time{font-size:10px;color:var(--sub);opacity:.7;margin:0 4px;}\n  .sys{text-align:center;color:var(--sub);font-size:12px;margin:8px 0;}\n\n  /* 输入区 */\n  #composer{display:flex;gap:8px;padding:10px 12px;background:var(--panel);\n    border-top:1px solid var(--line);align-items:center;}\n  #input{flex:1;padding:11px 14px;background:var(--panel2);border:1px solid var(--line);\n    border-radius:22px;color:var(--txt);font-size:15px;}\n  #input:focus{outline:none;border-color:var(--accent);}\n  #send{border:none;background:var(--accent);color:#fff;width:44px;height:44px;border-radius:50%;\n    cursor:pointer;font-size:18px;flex-shrink:0;}\n  #send:active{transform:scale(.92);}\n\n  /* ===== 侧边抽屉 ===== */\n  .drawer{position:fixed;inset:0;z-index:50;display:none;}\n  .drawer.on{display:block;}\n  .mask{position:absolute;inset:0;background:rgba(0,0,0,.5);}\n  .panel{position:absolute;right:0;top:0;bottom:0;width:82%;max-width:320px;background:var(--panel);\n    display:flex;flex-direction:column;animation:slide .2s ease;}\n  @keyframes slide{from{transform:translateX(100%);}to{transform:translateX(0);}}\n  .panel-h{display:flex;align-items:center;justify-content:space-between;padding:14px;\n    border-bottom:1px solid var(--line);}\n  .panel-h h3{font-size:16px;}\n  .panel-h button{border:none;background:none;color:var(--sub);font-size:22px;cursor:pointer;}\n  .panel-b{flex:1;overflow-y:auto;padding:8px;}\n  .panel-f{padding:12px;border-top:1px solid var(--line);}\n\n  .user-item{display:flex;align-items:center;gap:10px;padding:10px;border-radius:12px;cursor:pointer;}\n  .user-item:active{background:var(--panel2);}\n  .user-item .dot{width:9px;height:9px;border-radius:50%;background:var(--ok);flex-shrink:0;}\n  .user-item .dot.off{background:var(--line);}\n  .user-item .uname{min-width:0;flex:1;}\n  .user-item .un{flex:1;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}\n  .user-item .nk{font-size:11px;color:var(--sub);}\n  .user-item .tag{font-size:10px;padding:2px 6px;border-radius:6px;background:var(--panel2);color:var(--sub);}\n  .user-item .tag.admin{color:var(--accent);}\n  .user-item .tag.banned{color:var(--warn);}\n  .sec-title{font-size:12px;color:var(--sub);padding:12px 8px 4px;font-weight:600;}\n\n  .room-add{display:flex;gap:8px;margin-top:8px;}\n  .room-add input{flex:1;padding:9px 12px;background:var(--panel2);border:1px solid var(--line);\n    border-radius:10px;color:var(--txt);font-size:14px;}\n  .room-add button{padding:9px 14px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;}\n\n  .btn-block{width:100%;padding:11px;border-radius:12px;border:1px solid var(--line);background:var(--panel2);\n    color:var(--txt);font-size:14px;cursor:pointer;margin-bottom:8px;text-align:left;}\n  .btn-block:active{background:var(--line);}\n  .btn-block.danger{color:var(--warn);border-color:rgba(248,81,73,.3);}\n  .nick-edit{display:flex;gap:8px;}\n  .nick-edit input{flex:1;padding:10px 12px;background:var(--panel2);border:1px solid var(--line);\n    border-radius:10px;color:var(--txt);font-size:14px;}\n  .nick-edit button{padding:10px 16px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;}\n  .me-card{display:flex;align-items:center;gap:12px;padding:6px 0 14px;}\n  .me-card .avatar{width:46px;height:46px;font-size:16px;}\n  .me-card .info .n{font-weight:600;}\n  .me-card .info .u{font-size:12px;color:var(--sub);}\n  .logout{width:100%;padding:11px;border-radius:12px;border:1px solid var(--line);background:transparent;\n    color:var(--warn);font-size:14px;cursor:pointer;margin-top:10px;}\n</style>\n</head>\n<body>\n\n<!-- 登录/注册 -->\n<div id="screen-login">\n  <div class="auth">\n    <div class="logo">\n      <h1>💬 Termux 聊天室</h1>\n      <p>局域网内多人在线群聊 & 私聊</p>\n    </div>\n    <div class="tabs">\n      <button id="tab-login" class="on">登录</button>\n      <button id="tab-register">注册</button>\n<button id="tab-admin">管理员</button>\n    </div>\n    <div id="view-login">\n      <input id="login-user" placeholder="账号" autocomplete="off" autocapitalize="off">\n      <input id="login-pass" type="password" placeholder="密码">\n      <button class="submit" id="btn-login">进入聊天室</button>\n    </div>\n    <div id="view-admin" class="hidden">\n<input id="admin-user" placeholder="管理员账号">\n<input id="admin-pass" type="password" placeholder="密码">\n<button class="submit" id="btn-admin-login">进入管理后台</button>\n<p class="msg-line" id="admin-msg"></p>\n</div>\n<div id="view-register" class="hidden">\n      <input id="reg-user" placeholder="账号 (3-20位字母数字下划线)" autocomplete="off" autocapitalize="off">\n      <input id="reg-nick" placeholder="昵称 (可选)" autocomplete="off">\n      <input id="reg-pass" type="password" placeholder="密码 (至少4位)">\n      <button class="submit" id="btn-register">注册并登录</button>\n    </div>\n    <div class="msg-line" id="auth-msg"></div>\n    <p class="hint">后端运行在 Termux 上, 无需 pip 安装, 纯标准库实现<br>\n       手机 / 电脑连同一 WiFi 即可访问 <code id="hint-ip">同一局域网</code></p>\n  </div>\n</div>\n\n<!-- 聊天 -->\n<div id="screen-chat" class="hidden">\n  <header>\n    <div class="back">\n      <div class="title" id="cur-title">公共大厅</div>\n      <div class="sub" id="cur-sub">群聊</div>\n    </div>\n    <button class="icon-btn badge" id="btn-users" title="在线用户">👥<i></i></button>\n    <button class="icon-btn" id="btn-menu" title="菜单">☰</button>\n  </header>\n  <div id="room-tabs"></div>\n  <div id="messages"></div>\n  <div id="composer">\n    <input id="input" placeholder="说点什么…" autocomplete="off">\n    <button id="send">➤</button>\n  </div>\n</div>\n\n<!-- 用户/房间抽屉 -->\n<div class="drawer" id="drawer-users">\n  <div class="mask" data-close="drawer-users"></div>\n  <div class="panel">\n    <div class="panel-h"><h3>在线用户</h3><button data-close="drawer-users">✕</button></div>\n    <div class="panel-b" id="user-list"></div>\n  </div>\n</div>\n\n<!-- 菜单抽屉 -->\n<div class="drawer" id="drawer-menu">\n  <div class="mask" data-close="drawer-menu"></div>\n  <div class="panel">\n    <div class="panel-h"><h3>菜单</h3><button data-close="drawer-menu">✕</button></div>\n    <div class="panel-b">\n      <div class="me-card">\n        <div class="avatar" id="me-avatar">?</div>\n        <div class="info">\n          <div class="n" id="me-nick"></div>\n          <div class="u" id="me-user"></div>\n        </div>\n      </div>\n      <div class="sec-title">个人</div>\n      <div class="nick-edit">\n        <input id="nick-input" placeholder="新昵称">\n        <button id="btn-nick">保存</button>\n      </div>\n      <div class="sec-title">房间</div>\n      <div id="menu-rooms"></div>\n      <div class="room-add">\n        <input id="new-room" placeholder="新建房间 (管理员)">\n        <button id="btn-new-room">+</button>\n      </div>\n      <div class="sec-title">后台管理</div>\n      <button class="btn-block" id="btn-admin">👑 用户管理后台</button>\n    </div>\n    <div class="panel-f">\n      <button class="logout" id="btn-logout">退出登录</button>\n    </div>\n  </div>\n</div>\n\n<!-- 私聊浮窗 -->\n<div class="drawer" id="drawer-pm">\n  <div class="mask" data-close="drawer-pm"></div>\n  <div class="panel">\n    <div class="panel-h"><h3 id="pm-title">私聊</h3><button data-close="drawer-pm">✕</button></div>\n    <div class="panel-b" id="pm-messages"></div>\n    <div class="panel-f" style="display:flex;gap:8px;">\n      <input id="pm-input" placeholder="发私信…" autocomplete="off"\n             style="flex:1;padding:10px 14px;background:var(--panel2);border:1px solid var(--line);border-radius:22px;color:var(--txt);font-size:14px;">\n      <button id="pm-send" style="border:none;background:var(--accent);color:#fff;width:42px;height:42px;border-radius:50%;cursor:pointer;">➤</button>\n    </div>\n  </div>\n</div>\n\n<script src="app.js"></script>\n</body>\n</html>\n'
)
_EMBED_ADMIN = (
'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>聊天室管理后台</title>\n<style>\n  :root{--bg:#0f1419;--panel:#161d26;--panel2:#1c2531;--line:#2a3444;\n    --txt:#e6ebf2;--sub:#8a97a8;--accent:#4f9cff;--ok:#3fb950;--warn:#f85149;}\n  *{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;}\n  body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;\n    background:var(--bg);color:var(--txt);min-height:100vh;padding:16px;}\n  .wrap{max-width:720px;margin:0 auto;}\n  h1{font-size:20px;margin-bottom:4px;}\n  .sub{color:var(--sub);font-size:13px;margin-bottom:16px;}\n  .login{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;max-width:360px;margin:60px auto;}\n  .login input{width:100%;padding:12px;background:var(--panel2);border:1px solid var(--line);\n    border-radius:10px;color:var(--txt);font-size:15px;margin-bottom:12px;}\n  .login button{width:100%;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:15px;font-weight:600;}\n  .stats{display:flex;gap:10px;margin-bottom:16px;}\n  .stat{flex:1;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;text-align:center;}\n  .stat .n{font-size:24px;font-weight:700;}\n  .stat .l{font-size:12px;color:var(--sub);margin-top:4px;}\n  .card{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden;margin-bottom:16px;}\n  .card h2{font-size:15px;padding:14px;border-bottom:1px solid var(--line);}\n  table{width:100%;border-collapse:collapse;font-size:14px;}\n  th,td{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line);}\n  th{color:var(--sub);font-size:12px;font-weight:600;}\n  td.online{color:var(--ok);} td.offline{color:var(--sub);}\n  .mini{padding:5px 11px;border:1px solid var(--line);background:var(--panel2);color:var(--txt);\n    border-radius:8px;font-size:12px;cursor:pointer;margin-right:6px;}\n  .mini.danger{border-color:rgba(248,81,73,.3);color:var(--warn);}\n  .mini:active{opacity:.7;}\n  .hidden{display:none!important;}\n  .empty{color:var(--sub);font-size:13px;padding:20px;text-align:center;}\n  .logout{background:none;border:none;color:var(--sub);font-size:13px;cursor:pointer;text-decoration:underline;}\n  .head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;}\n</style>\n</head>\n<body>\n<div class="wrap">\n  <div id="login-box" class="login">\n    <h1>管理后台</h1>\n    <p class="sub" style="margin-bottom:18px;">仅管理员可访问</p>\n    <input id="adm-user" placeholder="管理员账号" value="admin">\n    <input id="adm-pass" type="password" placeholder="密码" value="admin123">\n    <button id="adm-go">登录</button>\n    <p class="sub" style="margin-top:14px;text-align:center;">默认 admin / admin123, 请登录后尽快修改密码</p>\n  </div>\n\n  <div id="admin-box" class="hidden">\n    <div class="head">\n      <h1>🛡️ 聊天室管理后台</h1>\n      <button class="logout" id="adm-out">退出</button>\n    </div>\n    <p class="sub">可查看所有用户、在线状态, 并封禁/解封账号</p>\n    <div class="stats">\n      <div class="stat"><div class="n" id="st-users">-</div><div class="l">注册用户</div></div>\n      <div class="stat"><div class="n" id="st-online">-</div><div class="l">在线</div></div>\n      <div class="stat"><div class="n" id="st-banned">-</div><div class="l">已封禁</div></div>\n    </div>\n    @@ADMIN_ROOMS_CARD@@\n      <div style="overflow-x:auto;">\n        <table>\n          <thead><tr><th>用户</th><th>昵称</th><th>状态</th><th>操作</th></tr></thead>\n          <tbody id="user-tbody"></tbody>\n        </table>\n      </div>\n    </div>\n  </div>\n</div>\n<script>\nvar admUser = "";\nfunction $(s){return document.querySelector(s);}\nfunction esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;"}[c];});}\nfunction post(path, data, cb){\n  fetch(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)})\n    .then(function(r){return r.json();}).then(cb).catch(function(){cb({ok:false,msg:"网络错误"});});\n}\n$("#adm-go").onclick = function(){\n  var u = $("#adm-user").value, p = $("#adm-pass").value;\n  var ADMIN_PAGE = "/admin";\npost("/api/login", {username:u, password:p}, function(j){\n    if(j.ok && j.user && j.user.is_admin){ admUser = u; $("#login-box").classList.add("hidden");\n      $("#admin-box").classList.remove("hidden"); load(); }\n    else if(j.ok){ alert("该账号不是管理员"); }\n    else { alert(j.msg); }\n  });\n};\n$("#adm-out").onclick = function(){ location.reload(); };\nfunction load(){\n  fetch("/api/admin/users?user=" + encodeURIComponent(admUser)).then(function(r){return r.json();}).then(function(j){\n    if(!j.ok){ alert(j.msg); return; }\n    var users = j.users, online = j.online;\n    var onSet = {}; online.forEach(function(o){onSet[o.username]=true;});\n    $("#st-users").textContent = users.length;\n    $("#st-online").textContent = online.length;\n    $("#st-banned").textContent = users.filter(function(u){return u.banned;}).length;\n    var tb = $("#user-tbody"); tb.innerHTML = "";\n    users.forEach(function(u){\n      var on = !!onSet[u.username];\n      var tr = document.createElement("tr");\n      tr.innerHTML = "<td>@" + esc(u.username) + (u.is_admin?\' <small style="color:var(--accent)">[管理]</small>\':"") + "</td>" +\n        "<td>" + esc(u.nickname) + "</td>" +\n        \'<td class="\' + (on?"online":"offline") + \'">\' + (on?"🟢 在线":"⚪ 离线") + "</td>" +\n        "<td>" + (u.is_admin? "<span style=\\"color:var(--sub);font-size:12px\\">—</span>"\n          : \'<button class="mini \' + (u.banned?"":"danger") + \'" data-u="\' + esc(u.username) + \'" data-ban="\' + !u.banned + \'">\'\n            + (u.banned?"解封":"封禁") + "</button>") + "</td>";\n      tb.appendChild(tr);\n    });\n  });\n}\n@@ADMIN_CLICK@@\n    var ban = b.dataset.ban === "true";\n    post("/api/admin/ban", {user:admUser, target:b.dataset.u, ban:ban}, function(j){\n      if(j.ok){ load(); } else { alert(j.msg); }\n    });\n  }\n});\nsetInterval(function(){ if(!$("#admin-box").classList.contains("hidden")) load(); }, 5000);\n</script>\n</body>\n</html>\n'
)
_EMBED_APPJS = (
'/* Termux 聊天室 - 前端逻辑 */\n(function () {\n  "use strict";\n\n  var API = "";   // 同源, 空字符串\n  var state = {\n    user: null,    // 当前登录账号\n    nickname: "",\n    isAdmin: false,\n    curRoom: "public",\n    rooms: [],\n    evSource: null,\n    historyDone: {},   // 房间名 -> bool\n  };\n\n  /* ---------- 工具 ---------- */\n  function $(s) { return document.querySelector(s); }\n  function el(html) {\n    var t = document.createElement("template");\n    t.innerHTML = html.trim();\n    return t.content.firstChild;\n  }\n  function esc(s) {\n    return String(s).replace(/[&<>"\']/g, function (c) {\n      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;" }[c];\n    });\n  }\n  function iniOf(name) { return (name && name.charAt(0).toUpperCase()) || "?"; }\n  function toast(msg, type) {\n    var old = document.getElementById("toast");\n    if (old) old.remove();\n    var d = el(\'<div id="toast" class="\' + (type || "") + \'">\' + esc(msg) + "</div>");\n    var st = d.style;\n    st.position = "fixed"; st.left = "50%"; st.bottom = "80px";\n    st.transform = "translateX(-50%)"; st.padding = "10px 18px";\n    st.background = "rgba(0,0,0,.85)"; st.color = "#fff";\n    st.borderRadius = "22px"; st.fontSize = "13px"; st.zIndex = "999";\n    st.transition = "opacity .25s";\n    document.body.appendChild(d);\n    setTimeout(function () { d.style.opacity = "0"; }, 1500);\n    setTimeout(function () { d.remove(); }, 1800);\n  }\n  function post(path, data, cb) {\n    fetch(API + path, {\n      method: "POST",\n      headers: { "Content-Type": "application/json" },\n      body: JSON.stringify(data || {}),\n    }).then(function (r) { return r.json(); })\n      .then(function (j) { cb && cb(j); })\n      .catch(function () { cb && cb({ ok: false, msg: "网络错误, 请检查后端是否运行" }); });\n  }\n\n  /* ---------- 登录/注册 ---------- */\n  function showAuthMsg(m, cls) {\n    var d = $("#auth-msg");\n    d.textContent = m;\n    d.className = "msg-line" + (cls ? " " + cls : "");\n  }\n  $("#tab-login").onclick = function () {\n    $("#tab-login").classList.add("on"); $("#tab-register").classList.remove("on"); $("#tab-admin").classList.remove("on");\n    $("#view-login").classList.remove("hidden"); $("#view-register").classList.add("hidden"); $("#view-admin").classList.add("hidden");\n    showAuthMsg("");\n  };\n  $("#tab-register").onclick = function () {\n    $("#tab-register").classList.add("on"); $("#tab-login").classList.remove("on"); $("#tab-admin").classList.remove("on");\n    $("#view-register").classList.remove("hidden"); $("#view-login").classList.add("hidden"); $("#view-admin").classList.add("hidden");\n    showAuthMsg("");\n  };\n  $("#tab-admin").onclick = function () {\n    $("#tab-admin").classList.add("on"); $("#tab-login").classList.remove("on"); $("#tab-register").classList.remove("on");\n    $("#view-admin").classList.remove("hidden"); $("#view-login").classList.add("hidden"); $("#view-register").classList.add("hidden");\n    showAuthMsg(""); showAdminMsg("");\n  };\n  function showAdminMsg(m, cls) {\n    var d = $("#admin-msg"); if (!d) return;\n    d.textContent = m; d.className = "msg-line" + (cls ? " " + cls : "");\n  }\n  $("#btn-admin-login").onclick = function () {\n    var u = $("#admin-user").value, p = $("#admin-pass").value;\n    if (!u || !p) return showAdminMsg("请填写管理员账号和密码", "err");\n    post("/api/login", { username: u, password: p }, function (j) {\n      if (j.ok && j.user && j.user.is_admin) {\n        localStorage.setItem("chat_user", j.user.username);\n        localStorage.setItem("chat_pass", p);\n        location.href = "/admin";\n      } else if (j.ok) {\n        showAdminMsg("该账号不是管理员, 已拒绝", "err");\n      } else {\n        showAdminMsg(j.msg, "err");\n      }\n    });\n  };\n  $("#btn-login").onclick = function () {\n    var u = $("#login-user").value, p = $("#login-pass").value;\n    if (!u || !p) return showAuthMsg("请填写账号和密码", "err");\n    post("/api/login", { username: u, password: p }, function (j) {\n      if (j.ok) { enter(j.user); } else { showAuthMsg(j.msg, "err"); }\n    });\n  };\n  $("#btn-register").onclick = function () {\n    var u = $("#reg-user").value, n = $("#reg-nick").value, p = $("#reg-pass").value;\n    if (!u || !p) return showAuthMsg("请填写账号和密码", "err");\n    post("/api/register", { username: u, password: p, nickname: n }, function (j) {\n      if (j.ok) {\n        showAuthMsg("注册成功, 正在登录…", "ok");\n        post("/api/login", { username: u, password: p }, function (k) {\n          if (k.ok) enter(k.user); else showAuthMsg(k.msg, "err");\n        });\n      } else { showAuthMsg(j.msg, "err"); }\n    });\n  };\n\n  function enter(user) {\n    state.user = user.username;\n    state.nickname = user.nickname;\n    state.isAdmin = user.is_admin;\n    localStorage.setItem("chat_user", user.username);\n    localStorage.setItem("chat_pass", $("#login-pass").value || $("#reg-pass").value);\n    $("#me-avatar").textContent = iniOf(user.nickname);\n    $("#me-nick").textContent = user.nickname;\n    $("#me-user").textContent = "@" + user.username + (user.is_admin ? " · 管理员" : "");\n    $("#screen-login").classList.add("hidden");\n    $("#screen-chat").classList.remove("hidden");\n    openStream();\n    loadRooms();\n  }\n\n  function tryAutoLogin() {\n    var u = localStorage.getItem("chat_user"), p = localStorage.getItem("chat_pass");\n    if (u && p) {\n      post("/api/login", { username: u, password: p }, function (j) {\n        if (j.ok) enter(j.user);\n      });\n    }\n  }\n\n  $("#btn-logout").onclick = function () {\n    localStorage.removeItem("chat_pass");\n    if (state.evSource) state.evSource.close();\n    location.reload();\n  };\n\n  /* ---------- SSE 长连接 ---------- */\n  function openStream() {\n    if (state.evSource) state.evSource.close();\n    state.evSource = new EventSource("/api/stream?user=" + encodeURIComponent(state.user));\n    state.evSource.onmessage = function (e) {\n      try { handleEvent(JSON.parse(e.data)); } catch (err) {}\n    };\n    state.evSource.onerror = function () {\n      // 后端断开后自动重连\n      setTimeout(openStream, 3000);\n    };\n  }\n\n  function handleEvent(ev) {\n    switch (ev.t) {\n      case "hello":\n        state.rooms = ev.rooms;\n        renderRoomTabs();\n        switchRoom("public");\n        renderOnline(ev.online || []);\n        break;\n      case "rooms":\n        state.rooms = ev.rooms;\n        renderRoomTabs();\n        break;\n      case "online":\n        renderOnline(ev.online || []);\n        break;\n      case "msg":\n        if (ev.mtype === "group") {\n          appendMsg(ev, ev.room === state.curRoom);\n          if (ev.room === state.curRoom) scrollBottom();\n        } else if (ev.mtype === "pm") {\n          handlePm(ev);\n        }\n        break;\n    }\n  }\n\n  /* ---------- 房间 ---------- */\n  function loadRooms() {\n    fetch("/api/rooms").then(function (r) { return r.json(); }).then(function (j) {\n      if (j.ok) { state.rooms = j.rooms; renderRoomTabs(); }\n    });\n  }\n  @@RENDERTABS_HDR@@<button class="room-tab\' + (r.id === state.curRoom ? " on" : "") + \'">\' + esc(r.name) + "</button>");\n      b.onclick = function () { switchRoom(r.id); };\n      box.appendChild(b);\n    });\n    // 菜单里的房间\n    var mb = $("#menu-rooms");\n    mb.innerHTML = "";\n    state.rooms.forEach(function (r) {\n      var btn = el(\'<button class="btn-block">\' + esc(r.name) + "</button>");\n      btn.onclick = function () {\n        post("/api/room/join", { user: state.user, room: r.id });\n        switchRoom(r.id);\n        closeDrawer("drawer-menu");\n      };\n      mb.appendChild(btn);\n    });\n  }\n  function switchRoom(id) {\n    state.curRoom = id;\n@@SWITCHROOM_NAME@@\n    $("#cur-title").textContent = r.name;\n    $("#cur-sub").textContent = "群聊 · " + id;\n    Array.prototype.forEach.call($$("#room-tabs .room-tab"), function (b, i) {\n      b.classList.toggle("on", state.rooms[i] && state.rooms[i].id === id);\n    });\n    $("#messages").innerHTML = "";\n    loadHistory(id);\n  }\n  function loadHistory(room) {\n    fetch("/api/history?room=" + encodeURIComponent(room))\n      .then(function (r) { return r.json(); })\n      .then(function (j) {\n        if (!j.ok) return;\n        $("#messages").innerHTML = "";\n        j.messages.forEach(function (m) {\n          appendMsg({ mtype: "group", user: m.from, room: m.to, text: m.content, time: m.time }, true);\n        });\n        scrollBottom();\n      });\n  }\n  function appendMsg(m, visible) {\n    if (!visible) return;\n    var mine = m.user === state.user;\n    var who = mine ? "我" : (m.user);\n    var d = el(\n      \'<div class="msg \' + (mine ? "me" : "") + (m.mtype === "pm" ? " pm" : "") + \'">\' +\n        \'<div class="avatar">\' + iniOf(who) + "</div>" +\n        \'<div><div class="who">\' + esc(who) + \'<span class="time">\' + esc(m.time || "") + "</span></div>" +\n        \'<div class="bubble">\' + esc(m.text) + "</div></div>" +\n      "</div>"\n    );\n    $("#messages").appendChild(d);\n  }\n  function scrollBottom() {\n    var m = $("#messages");\n    m.scrollTop = m.scrollHeight;\n  }\n\n  /* ---------- 发送 ---------- */\n  function send() {\n    var v = $("#input").value.trim();\n    if (!v) return;\n    post("/api/send", { user: state.user, room: state.curRoom, text: v });\n    $("#input").value = "";\n  }\n  $("#send").onclick = send;\n  $("#input").addEventListener("keydown", function (e) {\n    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }\n  });\n\n  /* ---------- 在线用户 ---------- */\n@@ONLINE_OLD@@\n      .then(function (r) { return r.json(); })\n      .then(function (j) {\n        if (!j.ok) return;\n        j.users.forEach(function (u) {\n          var on = !!onSet[u.username];\n          var tags = "";\n          if (u.is_admin) tags += \'<span class="tag admin">管理</span>\';\n          if (u.banned) tags += \'<span class="tag banned">已封</span>\';\n          var it = el(\n            \'<div class="user-item" data-u="\' + esc(u.username) + \'">\' +\n              \'<span class="dot\' + (on ? "" : " off") + \'"></span>\' +\n              \'<div class="uname"><div class="un">\' + esc(u.nickname) +\n              \' <small style="color:var(--sub)">@\' + esc(u.username) + "</small></div></div>" +\n              tags +\n            "</div>"\n          );\n          if (u.username !== state.user) {\n            it.onclick = function () { openPm(u.username, u.nickname); };\n          }\n          box.appendChild(it);\n        });\n        // 更新在线徽标\n        $("#btn-users").classList.toggle("on", list.length > 0);\n      })\n      .catch(function () {});\n  }\n\n  /* ---------- 私聊 ---------- */\n  var pmState = { to: null, toNick: "", log: [] };\n  function openPm(uname, nick) {\n    pmState.to = uname;\n    pmState.toNick = nick || uname;\n    pmState.log = [];\n    $("#pm-title").textContent = "私聊 · " + pmState.toNick;\n    $("#pm-messages").innerHTML = "";\n    closeDrawer("drawer-users");\n    openDrawer("drawer-pm");\n    // 把已有私聊历史(内存)展示\n  }\n  function handlePm(ev) {\n    var me = ev.user === state.user;\n    var who = me ? ("我 → " + pmState.toNick) : ev.user;\n    var d = el(\n      \'<div class="msg \' + (me ? "me" : "") + \' pm">\' +\n        \'<div class="avatar">\' + iniOf(ev.user) + "</div>" +\n        \'<div><div class="who">\' + esc(who) + \'<span class="time">\' + esc(ev.time || "") + "</span></div>" +\n        \'<div class="bubble">\' + esc(ev.text) + "</div></div>" +\n      "</div>"\n    );\n    if ($("#drawer-pm").classList.contains("on") && pmState.to === (me ? pmState.to : ev.user)) {\n      $("#pm-messages").appendChild(d);\n      var p = $("#pm-messages"); p.scrollTop = p.scrollHeight;\n    } else {\n      // 浮窗未开, 弹个提示\n      toast("新私信: " + ev.text.slice(0, 20));\n    }\n  }\n  function sendPm() {\n    var v = $("#pm-input").value.trim();\n    if (!v || !pmState.to) return;\n    post("/api/pm", { user: state.user, to: pmState.to, text: v });\n    $("#pm-input").value = "";\n  }\n  $("#pm-send").onclick = sendPm;\n  $("#pm-input").addEventListener("keydown", function (e) {\n    if (e.key === "Enter") { e.preventDefault(); sendPm(); }\n  });\n\n  /* ---------- 抽屉 ---------- */\n  function openDrawer(id) { $("#" + id).classList.add("on"); }\n  function closeDrawer(id) { $("#" + id).classList.remove("on"); }\n  document.addEventListener("click", function (e) {\n    var c = e.target.getAttribute && e.target.getAttribute("data-close");\n    if (c) closeDrawer(c);\n  });\n  $("#btn-users").onclick = function () { openDrawer("drawer-users"); };\n  $("#btn-menu").onclick = function () { openDrawer("drawer-menu"); };\n  $("#btn-admin").onclick = function () {\n    closeDrawer("drawer-menu");\n    if (state.isAdmin) window.open("admin.html", "_blank");\n    else toast("需要管理员权限", "err");\n  };\n  $("#btn-nick").onclick = function () {\n    var n = $("#nick-input").value.trim();\n    if (!n) return toast("昵称不能为空", "err");\n    post("/api/nickname", { user: state.user, nickname: n }, function (j) {\n      if (j.ok) { state.nickname = n; $("#me-nick").textContent = n; toast("昵称已更新", "ok"); }\n    });\n  };\n  $("#btn-new-room").onclick = function () {\n    var n = $("#new-room").value.trim();\n    if (!n) return;\n    post("/api/room/create", { user: state.user, name: n }, function (j) {\n      if (j.ok) { $("#new-room").value = ""; toast("房间已创建", "ok"); loadRooms(); }\n      else toast(j.msg, "err");\n    });\n  };\n\n  // 自动登录尝试\n  tryAutoLogin();\n\n  // 提示局域网地址\n  if (location.hostname && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {\n    $("#hint-ip").textContent = location.host;\n  }\n})();\n'
)

ADMIN_PASS = "admin123"                          # 初始管理员密码(首次登录后建议改)

os.makedirs(DB_DIR, exist_ok=True)

# ============ 数据库 (SQLite, 仅标准库) ============
class DB:
    def __init__(self, dbfile):
        self.dbfile = dbfile
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(dbfile), exist_ok=True)
        self._init_schema()

    def _conn(self):
        c = sqlite3.connect(self.dbfile)
        c.row_factory = sqlite3.Row
        return c

    def _init_schema(self):
        with self._lock:
            c = self._conn()
            c.execute("""CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY, password_hash TEXT,
                nickname TEXT, is_admin INTEGER DEFAULT 0, banned INTEGER DEFAULT 0)""")
            c.execute("""CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, type TEXT,
                "from" TEXT, "to" TEXT, content TEXT)""")
            c.execute('CREATE INDEX IF NOT EXISTS idx_msg_type_to ON messages(type, "to")')
            row = c.execute("SELECT 1 FROM users WHERE username=?", (ADMIN_USER,)).fetchone()
            if not row:
                c.execute("INSERT INTO users(username,password_hash,nickname,is_admin) VALUES(?,?,?,1)",
                          (ADMIN_USER, hashlib.sha256(ADMIN_PASS.encode()).hexdigest(), "管理员"))
            c.commit()
            c.close()

@@CHGPWD@@

    def register(self, username, password, nickname=""):
        username = username.strip().lower()
        if not re.match(r"^[a-zA-Z0-9_]{3,20}$", username):
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
                      (datetime.now().isoformat(timespec="seconds"), mtype, frm, to, content))
            c.commit(); c.close()

    def history(self, limit=300):
        c = self._conn()
        rows = c.execute('SELECT time,type,"from","to",content FROM messages '
                         "WHERE id>(SELECT MAX(id)-? FROM messages) ORDER BY id",
                         (limit,)).fetchall()
        c.close()
        return [dict(r) for r in rows]

    # ---- 兼容旧接口层 (字典式访问) ----
    class _UserView:
        def __init__(self, db): self.db = db
        def get(self, username, default=None):
            row = self.db._get(username)
            return row if row is not None else default
        def __contains__(self, username): return self.db._get(username) is not None
        def __getitem__(self, username):
            row = self.db._get(username)
            if row is None: raise KeyError(username)
            return row

    def _get(self, username):
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
        return self._UserView(self)

db = DB(os.path.join(DB_DIR, os.environ.get("DB_FILE", "chat.db").lstrip("/")))

# ============ 在线连接 & 消息分发 ============
class Client:
    def __init__(self, username, send):
        self.username = username
        self.nickname = db.users[username]["nickname"]
        self.send = send        # 发送函数: send(dict)
        self.rooms = set(["public"])

clients_lock = threading.Lock()
clients = {}        # username -> Client
@@ROOMMEMBERS@@

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
            return self._embed(_EMBED_INDEX, "text/html")
        if path == "/admin" or path == "/admin.html":
            return self._embed(_EMBED_ADMIN, "text/html")
        if path == "/app.js":
            return self._embed(_EMBED_APPJS, "application/javascript")
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

@@ROOMS@@

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
@@ADMINUSERS@@

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

@@CREATE@@
            if not db.users.get(user, {}).get("is_admin"):
                return self._json(403, {"ok": False, "msg": "需要管理员权限"})
            if not name:
                return self._json(400, {"ok": False, "msg": "房间名不能为空"})
            rid = "room_" + str(int(time.time()))
            with rooms_lock:
                rooms[rid] = name
            broadcast({"t": "rooms", "rooms": rooms_list()})
            return self._json(200, {"ok": True, "id": rid})

@@JOIN@@
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
    print("  后台:   /admin  (admin / admin123)")
    print("  Ctrl+C 停止")
    print("=" * 48)
    httpd = socketserver.ThreadingTCPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()
