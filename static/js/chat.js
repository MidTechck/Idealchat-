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
let opening = false;
let inboxSeq = 0;

function esc(text) {
  const d = document.createElement("div");
  d.textContent = text || "";
  return d.innerHTML;
}

function initials(name) {
  const t = (name || "?").trim();
  return (t[0] || "?").toUpperCase();
}

function avatarHtml(person, extra) {
  const klass = extra ? "avatar " + extra : "avatar";
  if (person && person.has_avatar && person.id) {
    return `<div class="${klass}"><img src="/avatar/${esc(person.id)}" alt=""></div>`;
  }
  return `<div class="${klass}">${esc(initials(person && person.display_name))}</div>`;
}

function fillAvatar(el, person) {
  if (!el) return;
  el.innerHTML = "";
  if (person && person.has_avatar && person.id) {
    const img = document.createElement("img");
    img.src = "/avatar/" + person.id;
    img.alt = "";
    el.appendChild(img);
  } else {
    el.textContent = initials(person && person.display_name);
  }
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

function showError(message) {
  let bar = $("#chat-error");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "chat-error";
    bar.className = "chat-error";
    shell.prepend(bar);
  }
  bar.textContent = message;
  bar.hidden = false;
  clearTimeout(showError._t);
  showError._t = setTimeout(() => {
    bar.hidden = true;
  }, 4000);
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
  if (activeConvo && socket && socket.connected) {
    socket.emit("leave_chat", { conversation_id: activeConvo });
  }
  activeConvo = null;
  activeOther = null;
  opening = false;
  shell.classList.remove("in-chat");
  document.body.classList.remove("in-chat");
  $("#thread").classList.remove("open");
  $("#composer").hidden = true;
  $("#block-btn").hidden = true;
  $("#thread-name").textContent = "Select a chat";
  $("#thread-status").textContent = "Private 1-to-1 messages";
  fillAvatar($("#thread-avatar"), null);
  $("#messages").innerHTML = `<div class="empty">Search a username, send a request, and chat after they accept.</div>`;
  loadInbox();
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (activeConvo) closeChat();
    showTab(btn.dataset.tab);
    if (btn.dataset.tab === "requests") loadRequests();
    if (btn.dataset.tab === "chats") loadInbox();
  });
});

$("#back").addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  closeChat();
});

$("#block-btn").addEventListener("click", async () => {
  if (!activeOther) return;
  if (!confirm("Block @" + activeOther.username + "? They will be hidden and cannot message you.")) return;
  try {
    await api("/api/block", { method: "POST", body: JSON.stringify({ username: activeOther.username }) });
    closeChat();
  } catch (err) {
    showError(err.message);
  }
});

$("#search").addEventListener("input", async (e) => {
  const q = e.target.value.trim();
  const box = $("#people-results");
  if (q.length < 2) {
    box.innerHTML = "";
    return;
  }
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(q));
    box.innerHTML =
      data.results
        .map((p) => {
          let action = "";
          if (p.relation === "friends") action = `<button class="btn" type="button" data-open="${esc(p.username)}">Chat</button>`;
          else if (p.relation === "outgoing") action = `<span class="tiny">Request sent</span>`;
          else if (p.relation === "incoming") action = `<span class="tiny">They requested you</span>`;
          else action = `<button class="btn" type="button" data-add="${esc(p.username)}">Add</button>`;
          return `<div class="item">${avatarHtml(p)}<div><strong>${esc(p.display_name)}</strong><div class="meta">@${esc(p.username)}</div></div>${action}</div>`;
        })
        .join("") || `<p class="empty">No people found.</p>`;
  } catch (err) {
    showError(err.message);
  }
});

document.body.addEventListener("click", async (e) => {
  const convoBtn = e.target.closest("[data-convo]");
  if (convoBtn && panes.chats.contains(convoBtn)) {
    e.preventDefault();
    openThread(convoBtn.dataset.convo);
    return;
  }
  const add = e.target.closest("[data-add]");
  if (add) {
    try {
      await api("/api/requests", { method: "POST", body: JSON.stringify({ username: add.dataset.add }) });
      add.replaceWith(Object.assign(document.createElement("span"), { className: "tiny", textContent: "Request sent" }));
    } catch (err) {
      showError(err.message);
    }
    return;
  }
  const open = e.target.closest("[data-open]");
  if (open) {
    try {
      const data = await api("/api/open/" + encodeURIComponent(open.dataset.open), { method: "POST" });
      await openThread(data.conversation_id);
    } catch (err) {
      showError(err.message);
    }
    return;
  }
  const accept = e.target.closest("[data-accept]");
  if (accept) {
    try {
      await api("/api/requests/" + accept.dataset.accept + "/accept", { method: "POST" });
      loadRequests();
      loadInbox();
    } catch (err) {
      showError(err.message);
    }
    return;
  }
  const decline = e.target.closest("[data-decline]");
  if (decline) {
    try {
      await api("/api/requests/" + decline.dataset.decline + "/decline", { method: "POST" });
      loadRequests();
    } catch (err) {
      showError(err.message);
    }
  }
});

async function loadInbox() {
  const seq = ++inboxSeq;
  try {
    const data = await api("/api/inbox");
    if (seq !== inboxSeq) return;
    if (!data.inbox.length) {
      panes.chats.innerHTML = `<p class="empty">No chats yet. Use People to find a username.</p>`;
      return;
    }
    panes.chats.innerHTML = data.inbox
      .map(
        (c) => `<button class="item ${c.id === activeConvo ? "on" : ""}" type="button" data-convo="${c.id}">
        ${avatarHtml(c.other)}
        <div><strong>${esc(c.other.display_name)}</strong><div class="meta">${esc(c.last_body || "No messages yet")}</div></div>
        ${c.unread ? `<span class="badge">${c.unread}</span>` : `<span class="tiny">${esc(formatTime(c.last_at))}</span>`}
      </button>`
      )
      .join("");
  } catch (err) {
    if (seq !== inboxSeq) return;
    showError(err.message);
  }
}

async function loadRequests() {
  try {
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
            ? `<div class="item">${avatarHtml(r.from)}<div><strong>${esc(r.from.display_name)}</strong><div class="meta">@${esc(r.from.username)}</div></div><button class="btn" type="button" data-accept="${r.id}">Accept</button><button class="btn ghost" type="button" data-decline="${r.id}">Decline</button></div>`
            : ""
        )
        .join("") +
      data.outgoing
        .map((r) =>
          r.to
            ? `<div class="item">${avatarHtml(r.to)}<div><strong>${esc(r.to.display_name)}</strong><div class="meta">Waiting for @${esc(r.to.username)}</div></div></div>`
            : ""
        )
        .join("");
  } catch (err) {
    showError(err.message);
  }
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
  if (!id) return;
  if (opening && activeConvo === id) return;
  opening = true;
  showTab("chats");
  shell.classList.add("in-chat");
  document.body.classList.add("in-chat");
  $("#thread").classList.add("open");
  $("#composer").hidden = false;
  $("#block-btn").hidden = false;
  $("#messages").innerHTML = `<div class="empty">Loading chat...</div>`;
  try {
    const data = await api("/api/thread/" + encodeURIComponent(id));
    if (activeConvo && socket && socket.connected && activeConvo !== data.conversation_id) {
      socket.emit("leave_chat", { conversation_id: activeConvo });
    }
    activeConvo = data.conversation_id;
    activeOther = data.other;
    $("#thread-name").textContent = data.other.display_name;
    $("#thread-status").textContent = statusLabel(data.other);
    fillAvatar($("#thread-avatar"), data.other);
    drawMessages(data.messages);
    if (socket && socket.connected) socket.emit("join_chat", { conversation_id: activeConvo });
    const item = panes.chats.querySelector('[data-convo="' + activeConvo + '"]');
    if (item) {
      const badge = item.querySelector(".badge");
      if (badge) badge.remove();
      panes.chats.querySelectorAll(".item").forEach((el) => el.classList.toggle("on", el === item));
    }
    setTimeout(() => {
      try {
        $("#draft").focus();
      } catch (e) {}
    }, 50);
  } catch (err) {
    shell.classList.remove("in-chat");
    document.body.classList.remove("in-chat");
    $("#composer").hidden = true;
    $("#block-btn").hidden = true;
    showError(err.message || "Could not open chat.");
  } finally {
    opening = false;
  }
}

const draft = $("#draft");
$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = draft.value.trim();
  if (!body || !activeConvo) return;
  draft.value = "";
  draft.style.height = "auto";
  try {
    if (socket && socket.connected) {
      socket.emit("send", { conversation_id: activeConvo, body });
    } else {
      const msg = await api("/api/send", {
        method: "POST",
        body: JSON.stringify({ conversation_id: activeConvo, body }),
      });
      appendLive(msg);
    }
  } catch (err) {
    draft.value = body;
    showError(err.message);
  }
});

draft.addEventListener("input", () => {
  draft.style.height = "auto";
  draft.style.height = Math.min(draft.scrollHeight, 120) + "px";
  if (socket && socket.connected && activeConvo) socket.emit("typing", { conversation_id: activeConvo });
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
  socket = io({ transports: ["polling", "websocket"], reconnection: true });
  socket.on("message", (msg) => {
    if (msg.conversation_id !== activeConvo) {
      loadInbox();
      return;
    }
    if (document.querySelector('.msg-row[data-id="' + msg.id + '"]')) return;
    appendLive(msg);
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
