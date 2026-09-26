const $ = (sel) => document.querySelector(sel);
const shell = $("#shell");
const panes = {
  chats: $("#pane-chats"),
  people: $("#pane-people"),
  requests: $("#pane-requests"),
};
let activeConvo = null;
let activeOther = null;
let socket = null;

function esc(text) {
  const d = document.createElement("div");
  d.textContent = text || "";
  return d.innerHTML;
}

function initials(name) {
  const t = (name || "?").trim();
  return (t[0] || "?").toUpperCase();
}

function isMine(msg) {
  if (msg && msg.mine === true) return true;
  if (msg && msg.mine === false) return false;
  return String((msg && msg.sender_id) || "") === String(window.ME || "");
}

function formatTime(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ticksFor(msg) {
  if (!isMine(msg) || msg.deleted) return "";
  const status = msg.status || "sent";
  return `<span class="ticks">${esc(status)}</span>`;
}

function bubbleHtml(msg, prev) {
  const mine = isMine(msg);
  const grouped = !!(prev && isMine(prev) === mine);
  const body = msg.deleted ? "<em>Message removed</em>" : esc(msg.body);
  const time = formatTime(msg.created_at);
  return `<div class="msg-row ${mine ? "mine" : "theirs"}${grouped ? " grouped" : ""}" data-id="${esc(msg.id)}" data-sender="${esc(msg.sender_id || "")}">
    <div class="bubble">
      <p class="msg-text">${body}</p>
      <div class="msg-meta"><time>${time}</time>${ticksFor(msg)}</div>
    </div>
  </div>`;
}

function lastMsg() {
  return $("#messages .msg-row:last-child");
}

async function api(url, opts) {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts && opts.headers) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  Object.entries(panes).forEach(([key, el]) => {
    el.hidden = key !== name;
  });
}

function closeChat() {
  if (activeConvo && socket) socket.emit("leave_chat", { conversation_id: activeConvo });
  activeConvo = null;
  activeOther = null;
  shell.classList.remove("in-chat");
  document.body.classList.remove("in-chat");
  $("#thread").classList.remove("open");
  $("#composer").hidden = true;
  $("#block-btn").hidden = true;
  $("#thread-name").textContent = "Select a chat";
  $("#thread-status").textContent = "Private 1-to-1 messages";
  $("#thread-avatar").textContent = "I";
  $("#messages").innerHTML = `<div class="empty">Search a username, send a request, and chat after they accept.</div>`;
  loadInbox();
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    closeChat();
    showTab(btn.dataset.tab);
    if (btn.dataset.tab === "requests") loadRequests();
    if (btn.dataset.tab === "chats") loadInbox();
  });
});

$("#back").addEventListener("click", (e) => {
  e.preventDefault();
  closeChat();
});

$("#block-btn").addEventListener("click", async () => {
  if (!activeOther) return;
  if (!confirm("Block @" + activeOther.username + "? They will be hidden and cannot message you.")) return;
  await api("/api/block", { method: "POST", body: JSON.stringify({ username: activeOther.username }) });
  closeChat();
});

$("#search").addEventListener("input", async (e) => {
  const q = e.target.value.trim();
  const box = $("#people-results");
  if (q.length < 2) {
    box.innerHTML = "";
    return;
  }
  const data = await api("/api/search?q=" + encodeURIComponent(q));
  box.innerHTML =
    data.results
      .map((p) => {
        let action = "";
        if (p.relation === "friends") action = `<button class="btn" data-open="${esc(p.username)}">Chat</button>`;
        else if (p.relation === "outgoing") action = `<span class="tiny">Request sent</span>`;
        else if (p.relation === "incoming") action = `<span class="tiny">They requested you</span>`;
        else action = `<button class="btn" data-add="${esc(p.username)}">Add</button>`;
        return `<div class="item"><div class="avatar">${esc(initials(p.display_name))}</div><div><strong>${esc(p.display_name)}</strong><div class="meta">@${esc(p.username)}</div></div>${action}</div>`;
      })
      .join("") || `<p class="empty">No people found.</p>`;
});

document.body.addEventListener("click", async (e) => {
  const add = e.target.closest("[data-add]");
  if (add) {
    await api("/api/requests", { method: "POST", body: JSON.stringify({ username: add.dataset.add }) });
    add.replaceWith(Object.assign(document.createElement("span"), { className: "tiny", textContent: "Request sent" }));
    return;
  }
  const open = e.target.closest("[data-open]");
  if (open) {
    const data = await api("/api/open/" + encodeURIComponent(open.dataset.open), { method: "POST" });
    openThread(data.conversation_id);
    return;
  }
  const accept = e.target.closest("[data-accept]");
  if (accept) {
    await api("/api/requests/" + accept.dataset.accept + "/accept", { method: "POST" });
    loadRequests();
    loadInbox();
    return;
  }
  const decline = e.target.closest("[data-decline]");
  if (decline) {
    await api("/api/requests/" + decline.dataset.decline + "/decline", { method: "POST" });
    loadRequests();
  }
});

async function loadInbox() {
  const data = await api("/api/inbox");
  if (!data.inbox.length) {
    panes.chats.innerHTML = `<p class="empty">No chats yet. Use People to find a username.</p>`;
    return;
  }
  panes.chats.innerHTML = data.inbox
    .map(
      (c) => `<button class="item ${c.id === activeConvo ? "on" : ""}" data-convo="${c.id}">
        <div class="avatar">${esc(initials(c.other.display_name))}</div>
        <div><strong>${esc(c.other.display_name)}</strong><div class="meta">${esc(c.last_body || "No messages yet")}</div></div>
        ${c.unread ? `<span class="badge">${c.unread}</span>` : `<span class="tiny">${esc(formatTime(c.last_at))}</span>`}
      </button>`
    )
    .join("");
  panes.chats.querySelectorAll("[data-convo]").forEach((btn) => {
    btn.addEventListener("click", () => openThread(btn.dataset.convo));
  });
}

async function loadRequests() {
  const data = await api("/api/requests");
  const n = data.incoming.length;
  $("#req-count").textContent = n ? "(" + n + ")" : "";
  if (!data.incoming.length && !data.outgoing.length) {
    panes.requests.innerHTML = `<p class="empty">No friend requests.</p>`;
    return;
  }
  panes.requests.innerHTML =
    data.incoming
      .map((r) =>
        r.from
          ? `<div class="item"><div class="avatar">${esc(initials(r.from.display_name))}</div><div><strong>${esc(r.from.display_name)}</strong><div class="meta">@${esc(r.from.username)}</div></div><button class="btn" data-accept="${r.id}">Accept</button><button class="btn ghost" data-decline="${r.id}">Decline</button></div>`
          : ""
      )
      .join("") +
    data.outgoing
      .map((r) =>
        r.to
          ? `<div class="item"><div class="avatar">${esc(initials(r.to.display_name))}</div><div><strong>${esc(r.to.display_name)}</strong><div class="meta">Waiting for @${esc(r.to.username)}</div></div></div>`
          : ""
      )
      .join("");
}

function statusLabel(other) {
  if (!other) return "";
  if (other.online) return "Online";
  if (other.last_seen_at) return "Last seen " + new Date(other.last_seen_at).toLocaleString();
  return "Offline";
}

function drawMessages(list) {
  const box = $("#messages");
  if (!list.length) {
    box.innerHTML = `<div class="empty">No messages yet.</div>`;
    return;
  }
  box.innerHTML = list.map((msg, i) => bubbleHtml(msg, list[i - 1])).join("");
  box.scrollTop = box.scrollHeight;
}

async function openThread(id) {
  const data = await api("/api/thread/" + id);
  if (activeConvo && socket && activeConvo !== data.conversation_id) {
    socket.emit("leave_chat", { conversation_id: activeConvo });
  }
  activeConvo = data.conversation_id;
  activeOther = data.other;
  shell.classList.add("in-chat");
  document.body.classList.add("in-chat");
  $("#thread").classList.add("open");
  $("#thread-name").textContent = data.other.display_name;
  $("#thread-status").textContent = statusLabel(data.other);
  $("#thread-avatar").textContent = initials(data.other.display_name);
  $("#block-btn").hidden = false;
  $("#composer").hidden = false;
  drawMessages(data.messages);
  if (socket) socket.emit("join_chat", { conversation_id: activeConvo });
  loadInbox();
  $("#draft").focus();
}

const draft = $("#draft");
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const body = draft.value.trim();
  if (!body || !activeConvo || !socket) return;
  socket.emit("send", { conversation_id: activeConvo, body });
  draft.value = "";
  draft.style.height = "auto";
});

draft.addEventListener("input", () => {
  draft.style.height = "auto";
  draft.style.height = Math.min(draft.scrollHeight, 120) + "px";
  if (socket && activeConvo) socket.emit("typing", { conversation_id: activeConvo });
});

draft.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});

function appendLive(msg) {
  const empty = $("#messages .empty");
  if (empty) empty.remove();
  const prevEl = lastMsg();
  const prev = prevEl
    ? { sender_id: prevEl.getAttribute("data-sender"), mine: prevEl.classList.contains("mine") }
    : null;
  $("#messages").insertAdjacentHTML("beforeend", bubbleHtml(msg, prev));
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

function connect() {
  socket = io({ transports: ["websocket", "polling"] });
  socket.on("message", (msg) => {
    if (msg.conversation_id !== activeConvo) {
      loadInbox();
      return;
    }
    if (document.querySelector('.msg-row[data-id="' + msg.id + '"]')) return;
    appendLive(msg);
    loadInbox();
  });
  socket.on("inbox_ping", () => loadInbox());
  socket.on("request_update", () => loadRequests());
  socket.on("typing", (p) => {
    if (p.conversation_id === activeConvo && String(p.user_id) !== String(window.ME)) {
      $("#thread-status").textContent = "Typing...";
      setTimeout(() => {
        if (activeOther) $("#thread-status").textContent = statusLabel(activeOther);
      }, 2500);
    }
  });
  socket.on("read", (p) => {
    if (p.conversation_id !== activeConvo) return;
    document.querySelectorAll(".msg-row.mine .ticks").forEach((el) => {
      el.textContent = "read";
    });
  });
  socket.on("blocked", closeChat);
}

connect();
loadInbox();
loadRequests();
