import os
from datetime import timedelta
from functools import wraps

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room

from database import (
    BIO_MAX,
    MESSAGE_MAX,
    NAME_MAX,
    STATUS_MAX,
    USERNAME_RE,
    Block,
    Conversation,
    ConversationMember,
    FriendRequest,
    Friendship,
    Message,
    MessageHide,
    User,
    are_friends,
    blocked_either_way,
    can_chat,
    db,
    get_or_create_conversation,
    message_payload,
    new_id,
    normalize_username,
    pair,
    relation_of,
    sqlalchemy_url,
    touch_user,
    utcnow,
)

ROOT = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(ROOT, "templates"),
    static_folder=os.path.join(ROOT, "static"),
)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "idealchat-dev-change-me"
app.config["SQLALCHEMY_DATABASE_URI"] = sqlalchemy_url()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", manage_session=False)

online_sids = {}


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Sign in required"}), 401
            return redirect(url_for("login"))
        g.user = user
        touch_user(user)
        db.session.commit()
        return view(*args, **kwargs)

    return wrapped


def json_error(message, code=400):
    return jsonify({"error": message}), code


@app.before_request
def load_user():
    g.user = current_user()


@app.route("/health")
def health():
    return "ok", 200


@app.route("/")
def home():
    if g.user:
        return redirect(url_for("chat"))
    return render_template("login.html", mode="home")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("chat"))
    error = None
    if request.method == "POST":
        username = normalize_username(request.form.get("username"))
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            error = "Username or password is wrong."
        else:
            session.permanent = True
            session["user_id"] = user.id
            touch_user(user)
            db.session.commit()
            return redirect(url_for("chat"))
    return render_template("login.html", mode="login", error=error)


@app.route("/signin", methods=["GET", "POST"])
def signin():
    if g.user:
        return redirect(url_for("chat"))
    error = None
    if request.method == "POST":
        username = normalize_username(request.form.get("username"))
        display = (request.form.get("display_name") or "").strip()
        password = request.form.get("password") or ""
        if not USERNAME_RE.match(username):
            error = "Username must be 3 to 20 letters, numbers, or underscores."
        elif not display or len(display) > NAME_MAX:
            error = "Display name is required and must stay under 40 characters."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif User.query.filter_by(username=username).first():
            error = "That username is taken."
        else:
            user = User(
                id=new_id(),
                username=username,
                display_name=display,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session.permanent = True
            session["user_id"] = user.id
            return redirect(url_for("chat"))
    return render_template("Signin.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/chat")
@login_required
def chat():
    return render_template("Chat.html", me=g.user)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    error = None
    saved = False
    if request.method == "POST":
        display = (request.form.get("display_name") or "").strip()
        bio = (request.form.get("bio") or "").strip()
        status = (request.form.get("status_text") or "").strip()
        if not display or len(display) > NAME_MAX:
            error = "Display name is required and must stay under 40 characters."
        elif len(bio) > BIO_MAX:
            error = "Bio must stay under 160 characters."
        elif len(status) > STATUS_MAX:
            error = "Status must stay under 80 characters."
        else:
            g.user.display_name = display
            g.user.bio = bio
            g.user.status_text = status
            db.session.commit()
            saved = True
    blocked = (
        db.session.query(User)
        .join(Block, Block.blocked_id == User.id)
        .filter(Block.blocker_id == g.user.id)
        .order_by(User.username)
        .all()
    )
    return render_template("Profile.html", me=g.user, error=error, saved=saved, blocked=blocked)


@app.route("/api/me")
@login_required
def api_me():
    return jsonify(g.user.public("self"))


@app.route("/api/search")
@login_required
def api_search():
    q = normalize_username(request.args.get("q"))
    if len(q) < 2:
        return jsonify({"results": []})
    rows = (
        User.query.filter(User.username.contains(q), User.id != g.user.id)
        .order_by(User.username)
        .limit(20)
        .all()
    )
    results = []
    for user in rows:
        rel = relation_of(g.user.id, user.id)
        if rel == "none" and blocked_either_way(g.user.id, user.id):
            continue
        if rel == "blocked":
            continue
        results.append(user.public(rel))
    return jsonify({"results": results})


@app.route("/api/requests", methods=["GET", "POST"])
@login_required
def api_requests():
    if request.method == "GET":
        incoming = FriendRequest.query.filter_by(to_user_id=g.user.id).all()
        outgoing = FriendRequest.query.filter_by(from_user_id=g.user.id).all()
        people = {u.id: u for u in User.query.filter(
            User.id.in_([r.from_user_id for r in incoming] + [r.to_user_id for r in outgoing] or ["_"])
        ).all()}
        return jsonify({
            "incoming": [
                {
                    "id": r.id,
                    "from": people[r.from_user_id].public("incoming") if r.from_user_id in people else None,
                }
                for r in incoming
            ],
            "outgoing": [
                {
                    "id": r.id,
                    "to": people[r.to_user_id].public("outgoing") if r.to_user_id in people else None,
                }
                for r in outgoing
            ],
        })

    data = request.get_json(silent=True) or {}
    username = normalize_username(data.get("username"))
    other = User.query.filter_by(username=username).first()
    if not other or other.id == g.user.id:
        return json_error("No user with that username.")
    if blocked_either_way(g.user.id, other.id):
        return json_error("You cannot send a request to that person.")
    if are_friends(g.user.id, other.id):
        return json_error("You are already friends.")
    incoming = FriendRequest.query.filter_by(from_user_id=other.id, to_user_id=g.user.id).first()
    if incoming:
        return json_error("They already sent you a request. Accept it from Requests.")
    existing = FriendRequest.query.filter_by(from_user_id=g.user.id, to_user_id=other.id).first()
    if existing:
        return jsonify({"ok": True, "already": True})
    req = FriendRequest(id=new_id(), from_user_id=g.user.id, to_user_id=other.id)
    db.session.add(req)
    db.session.commit()
    socketio.emit("request_update", {"kind": "incoming"}, room="user:" + other.id)
    return jsonify({"ok": True})


@app.route("/api/requests/<req_id>/<action>", methods=["POST"])
@login_required
def api_request_action(req_id, action):
    req = db.session.get(FriendRequest, req_id)
    if not req:
        return json_error("Request not found.", 404)
    if action == "accept":
        if req.to_user_id != g.user.id:
            return json_error("Not allowed.", 403)
        lo, hi = pair(req.from_user_id, req.to_user_id)
        if not Friendship.query.filter_by(user_a=lo, user_b=hi).first():
            db.session.add(Friendship(user_a=lo, user_b=hi))
        get_or_create_conversation(req.from_user_id, req.to_user_id)
        other_id = req.from_user_id
        db.session.delete(req)
        db.session.commit()
        socketio.emit("request_update", {"kind": "accepted"}, room="user:" + other_id)
        return jsonify({"ok": True})
    if action == "decline":
        if req.to_user_id != g.user.id:
            return json_error("Not allowed.", 403)
        other_id = req.from_user_id
        db.session.delete(req)
        db.session.commit()
        socketio.emit("request_update", {"kind": "declined"}, room="user:" + other_id)
        return jsonify({"ok": True})
    if action == "cancel":
        if req.from_user_id != g.user.id:
            return json_error("Not allowed.", 403)
        other_id = req.to_user_id
        db.session.delete(req)
        db.session.commit()
        socketio.emit("request_update", {"kind": "cancelled"}, room="user:" + other_id)
        return jsonify({"ok": True})
    return json_error("Unknown action.")


@app.route("/api/block", methods=["POST"])
@login_required
def api_block():
    data = request.get_json(silent=True) or {}
    username = normalize_username(data.get("username"))
    other = User.query.filter_by(username=username).first()
    if not other or other.id == g.user.id:
        return json_error("No user with that username.")
    if Block.query.filter_by(blocker_id=g.user.id, blocked_id=other.id).first():
        return jsonify({"ok": True})
    db.session.add(Block(blocker_id=g.user.id, blocked_id=other.id))
    FriendRequest.query.filter(
        ((FriendRequest.from_user_id == g.user.id) & (FriendRequest.to_user_id == other.id))
        | ((FriendRequest.from_user_id == other.id) & (FriendRequest.to_user_id == g.user.id))
    ).delete(synchronize_session=False)
    lo, hi = pair(g.user.id, other.id)
    Friendship.query.filter_by(user_a=lo, user_b=hi).delete()
    db.session.commit()
    socketio.emit("blocked", {"by": g.user.username}, room="user:" + other.id)
    return jsonify({"ok": True})


@app.route("/api/unblock", methods=["POST"])
@login_required
def api_unblock():
    data = request.get_json(silent=True) or {}
    username = normalize_username(data.get("username"))
    other = User.query.filter_by(username=username).first()
    if not other:
        return json_error("No user with that username.")
    Block.query.filter_by(blocker_id=g.user.id, blocked_id=other.id).delete()
    db.session.commit()
    return jsonify({"ok": True})


def _inbox_for(user):
    convos = Conversation.query.filter(
        (Conversation.user_a == user.id) | (Conversation.user_b == user.id)
    ).all()
    items = []
    for convo in convos:
        other_id = convo.other_id(user.id)
        if Block.query.filter_by(blocker_id=user.id, blocked_id=other_id).first():
            continue
        other = db.session.get(User, other_id)
        if not other:
            continue
        last = (
            Message.query.filter_by(conversation_id=convo.id, deleted_for_everyone=False)
            .outerjoin(
                MessageHide,
                (MessageHide.message_id == Message.id) & (MessageHide.user_id == user.id),
            )
            .filter(MessageHide.message_id.is_(None))
            .order_by(Message.created_at.desc())
            .first()
        )
        mine = ConversationMember.query.filter_by(
            conversation_id=convo.id, user_id=user.id
        ).first()
        unread = 0
        if mine and mine.last_read_at:
            unread = (
                Message.query.filter(
                    Message.conversation_id == convo.id,
                    Message.sender_id != user.id,
                    Message.deleted_for_everyone.is_(False),
                    Message.created_at > mine.last_read_at,
                ).count()
            )
        elif last and last.sender_id != user.id:
            unread = (
                Message.query.filter(
                    Message.conversation_id == convo.id,
                    Message.sender_id != user.id,
                    Message.deleted_for_everyone.is_(False),
                ).count()
            )
        items.append(
            {
                "id": convo.id,
                "other": other.public("friends"),
                "last_body": "" if not last else last.body,
                "last_at": last.created_at.isoformat() if last and last.created_at else convo.created_at.isoformat(),
                "unread": unread,
            }
        )
    items.sort(key=lambda x: x["last_at"] or "", reverse=True)
    return items


@app.route("/api/inbox")
@login_required
def api_inbox():
    return jsonify({"inbox": _inbox_for(g.user)})


@app.route("/api/thread/<convo_id>")
@login_required
def api_thread(convo_id):
    convo = db.session.get(Conversation, convo_id)
    if not convo or g.user.id not in (convo.user_a, convo.user_b):
        return json_error("Chat not found.", 404)
    other_id = convo.other_id(g.user.id)
    if Block.query.filter_by(blocker_id=g.user.id, blocked_id=other_id).first():
        return json_error("This person is blocked.", 403)
    if not can_chat(g.user.id, other_id):
        return json_error("You can only chat after both of you are friends.")
    other = db.session.get(User, other_id)
    hidden_ids = {
        h.message_id
        for h in MessageHide.query.filter_by(user_id=g.user.id).all()
    }
    messages = (
        Message.query.filter_by(conversation_id=convo.id)
        .order_by(Message.created_at.asc())
        .limit(400)
        .all()
    )
    other_member = ConversationMember.query.filter_by(
        conversation_id=convo.id, user_id=other_id
    ).first()
    other_last_read = other_member.last_read_at if other_member else None
    delivered = other_id in online_sids
    payload = [
        message_payload(m, g.user.id, other_last_read, delivered)
        for m in messages
        if m.id not in hidden_ids
    ]
    mine = ConversationMember.query.filter_by(
        conversation_id=convo.id, user_id=g.user.id
    ).first()
    if mine:
        mine.last_read_at = utcnow()
        db.session.commit()
        socketio.emit(
            "read",
            {"conversation_id": convo.id, "reader_id": g.user.id, "at": mine.last_read_at.isoformat()},
            room="convo:" + convo.id,
        )
    typing = False
    if other_member and other_member.typing_until:
        typing_until = other_member.typing_until
        if typing_until.tzinfo is None:
            from datetime import timezone
            typing_until = typing_until.replace(tzinfo=timezone.utc)
        typing = typing_until > utcnow()
    return jsonify(
        {
            "conversation_id": convo.id,
            "other": other.public("friends"),
            "messages": payload,
            "typing": typing,
        }
    )


@app.route("/api/open/<username>", methods=["POST"])
@login_required
def api_open(username):
    other = User.query.filter_by(username=normalize_username(username)).first()
    if not other:
        return json_error("No user with that username.")
    if not can_chat(g.user.id, other.id):
        return json_error("You can only chat after they accept your friend request.")
    convo = get_or_create_conversation(g.user.id, other.id)
    return jsonify({"conversation_id": convo.id})


with app.app_context():
    db.create_all()


def _user_from_sid():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.session.get(User, uid)


@socketio.on("connect")
def on_connect():
    user = _user_from_sid()
    if not user:
        return False
    online_sids.setdefault(user.id, set()).add(request.sid)
    join_room("user:" + user.id)
    touch_user(user)
    db.session.commit()
    emit("presence", {"user_id": user.id, "online": True}, broadcast=True)


@socketio.on("disconnect")
def on_disconnect():
    user = _user_from_sid()
    if not user:
        return
    sids = online_sids.get(user.id, set())
    sids.discard(request.sid)
    if not sids:
        online_sids.pop(user.id, None)
        touch_user(user)
        db.session.commit()
        emit("presence", {"user_id": user.id, "online": False}, broadcast=True)


@socketio.on("join_chat")
def on_join_chat(data):
    user = _user_from_sid()
    if not user:
        return
    convo_id = (data or {}).get("conversation_id")
    convo = db.session.get(Conversation, convo_id) if convo_id else None
    if not convo or user.id not in (convo.user_a, convo.user_b):
        return
    join_room("convo:" + convo.id)
    member = ConversationMember.query.filter_by(
        conversation_id=convo.id, user_id=user.id
    ).first()
    if member:
        member.last_read_at = utcnow()
        db.session.commit()
        emit(
            "read",
            {"conversation_id": convo.id, "reader_id": user.id, "at": member.last_read_at.isoformat()},
            room="convo:" + convo.id,
        )


@socketio.on("leave_chat")
def on_leave_chat(data):
    convo_id = (data or {}).get("conversation_id")
    if convo_id:
        leave_room("convo:" + convo_id)


@socketio.on("typing")
def on_typing(data):
    user = _user_from_sid()
    if not user:
        return
    convo_id = (data or {}).get("conversation_id")
    convo = db.session.get(Conversation, convo_id) if convo_id else None
    if not convo or user.id not in (convo.user_a, convo.user_b):
        return
    member = ConversationMember.query.filter_by(
        conversation_id=convo.id, user_id=user.id
    ).first()
    if member:
        from datetime import timedelta
        member.typing_until = utcnow() + timedelta(seconds=3)
        db.session.commit()
    emit(
        "typing",
        {"conversation_id": convo.id, "user_id": user.id, "on": True},
        room="convo:" + convo.id,
        include_self=False,
    )


@socketio.on("send")
def on_send(data):
    user = _user_from_sid()
    if not user:
        return
    data = data or {}
    convo_id = data.get("conversation_id")
    body = (data.get("body") or "").strip()
    if not body or len(body) > MESSAGE_MAX:
        emit("error", {"error": "Message is empty or too long."})
        return
    convo = db.session.get(Conversation, convo_id) if convo_id else None
    if not convo or user.id not in (convo.user_a, convo.user_b):
        emit("error", {"error": "Chat not found."})
        return
    other_id = convo.other_id(user.id)
    if not can_chat(user.id, other_id):
        emit("error", {"error": "You cannot send messages to this person."})
        return
    msg = Message(
        id=new_id(),
        conversation_id=convo.id,
        sender_id=user.id,
        body=body,
        reply_to_id=data.get("reply_to_id") or None,
    )
    db.session.add(msg)
    db.session.commit()
    delivered = other_id in online_sids
    payload_self = message_payload(msg, user.id, None, delivered)
    payload_peer = message_payload(msg, other_id, None, delivered)
    emit("message", payload_self)
    socketio.emit("message", payload_peer, room="user:" + other_id)
    socketio.emit(
        "inbox_ping",
        {
            "conversation_id": convo.id,
            "preview": body[:80],
            "from_id": user.id,
        },
        room="user:" + other_id,
    )


@socketio.on("delete")
def on_delete(data):
    user = _user_from_sid()
    if not user:
        return
    msg = db.session.get(Message, (data or {}).get("message_id"))
    if not msg or msg.sender_id != user.id:
        return
    msg.deleted_for_everyone = True
    msg.body = ""
    db.session.commit()
    emit(
        "deleted",
        {"id": msg.id, "conversation_id": msg.conversation_id},
        room="convo:" + msg.conversation_id,
    )
