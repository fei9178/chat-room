/* Termux 聊天室 - 前端逻辑 */
(function () {
  "use strict";

  var API = "";   // 同源, 空字符串
  var state = {
    user: null,    // 当前登录账号
    nickname: "",
    isAdmin: false,
    curRoom: "public",
    rooms: [],
    evSource: null,
    historyDone: {},   // 房间名 -> bool
  };

  /* ---------- 工具 ---------- */
  function $(s) { return document.querySelector(s); }
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

  /* ---------- 登录/注册 ---------- */
  function showAuthMsg(m, cls) {
    var d = $("#auth-msg");
    d.textContent = m;
    d.className = "msg-line" + (cls ? " " + cls : "");
  }
  $("#tab-login").onclick = function () {
    $("#tab-login").classList.add("on"); $("#tab-register").classList.remove("on");
    $("#view-login").classList.remove("hidden"); $("#view-register").classList.add("hidden");
    showAuthMsg("");
  };
  $("#tab-register").onclick = function () {
    $("#tab-register").classList.add("on"); $("#tab-login").classList.remove("on");
    $("#view-register").classList.remove("hidden"); $("#view-login").classList.add("hidden");
    showAuthMsg("");
  };
  $("#btn-login").onclick = function () {
    var u = $("#login-user").value, p = $("#login-pass").value;
    if (!u || !p) return showAuthMsg("请填写账号和密码", "err");
    post("/api/login", { username: u, password: p }, function (j) {
      if (j.ok) { enter(j.user); } else { showAuthMsg(j.msg, "err"); }
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

  /* ---------- SSE 长连接 ---------- */
  function openStream() {
    if (state.evSource) state.evSource.close();
    state.evSource = new EventSource("/api/stream?user=" + encodeURIComponent(state.user));
    state.evSource.onmessage = function (e) {
      try { handleEvent(JSON.parse(e.data)); } catch (err) {}
    };
    state.evSource.onerror = function () {
      // 后端断开后自动重连
      setTimeout(openStream, 3000);
    };
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

  /* ---------- 房间 ---------- */
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
    // 菜单里的房间
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
    var r = state.rooms.filter(function (x) { return x.id === id; })[0] || { name: "公共大厅" };
    $("#cur-title").textContent = r.name;
    $("#cur-sub").textContent = "群聊 · " + id;
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
    var who = mine ? "我" : (m.user);
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

  /* ---------- 发送 ---------- */
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

  /* ---------- 在线用户 ---------- */
  function renderOnline(list) {
    var box = $("#user-list");
    var onSet = {};
    list.forEach(function (u) { onSet[u.username] = true; });
    box.innerHTML = "";
    fetch("/api/admin/users?user=" + encodeURIComponent(state.user))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) return;
        j.users.forEach(function (u) {
          var on = !!onSet[u.username];
          var tags = "";
          if (u.is_admin) tags += '<span class="tag admin">管理</span>';
          if (u.banned) tags += '<span class="tag banned">已封</span>';
          var it = el(
            '<div class="user-item" data-u="' + esc(u.username) + '">' +
              '<span class="dot' + (on ? "" : " off") + '"></span>' +
              '<div class="uname"><div class="un">' + esc(u.nickname) +
              ' <small style="color:var(--sub)">@' + esc(u.username) + "</small></div></div>" +
              tags +
            "</div>"
          );
          if (u.username !== state.user) {
            it.onclick = function () { openPm(u.username, u.nickname); };
          }
          box.appendChild(it);
        });
        // 更新在线徽标
        $("#btn-users").classList.toggle("on", list.length > 0);
      })
      .catch(function () {});
  }

  /* ---------- 私聊 ---------- */
  var pmState = { to: null, toNick: "", log: [] };
  function openPm(uname, nick) {
    pmState.to = uname;
    pmState.toNick = nick || uname;
    pmState.log = [];
    $("#pm-title").textContent = "私聊 · " + pmState.toNick;
    $("#pm-messages").innerHTML = "";
    closeDrawer("drawer-users");
    openDrawer("drawer-pm");
    // 把已有私聊历史(内存)展示
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
      // 浮窗未开, 弹个提示
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

  /* ---------- 抽屉 ---------- */
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
    if (state.isAdmin) window.open("admin.html", "_blank");
    else toast("需要管理员权限", "err");
  };
  $("#btn-nick").onclick = function () {
    var n = $("#nick-input").value.trim();
    if (!n) return toast("昵称不能为空", "err");
    post("/api/nickname", { user: state.user, nickname: n }, function (j) {
      if (j.ok) { state.nickname = n; $("#me-nick").textContent = n; toast("昵称已更新", "ok"); }
    });
  };
  $("#btn-new-room").onclick = function () {
    var n = $("#new-room").value.trim();
    if (!n) return;
    post("/api/room/create", { user: state.user, name: n }, function (j) {
      if (j.ok) { $("#new-room").value = ""; toast("房间已创建", "ok"); loadRooms(); }
      else toast(j.msg, "err");
    });
  };

  // 自动登录尝试
  tryAutoLogin();

  // 提示局域网地址
  if (location.hostname && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    $("#hint-ip").textContent = location.host;
  }
})();
