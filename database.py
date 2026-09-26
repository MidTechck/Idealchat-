import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_, or_, func
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
ONLINE_WINDOW = timedelta(seconds=45)
MESSAGE_MAX = 4000
NAME_MAX = 40
BIO_MAX = 160
STATUS_MAX = 80


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return uuid.uuid4().hex


def normalize_username(value):
    return (value or "").strip().lower()


def pair(a, b):
    return (a, b) if a < b else (b, a)


def sqlalchemy_url():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        root = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(root, "instance", "idealchat.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return "sqlite:///" + path
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(32), primary_key=True, default=new_id)
    username = db.Column(db.String(20), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(40), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    bio = db.Column(db.String(160), nullable=False, default="")
    status_text = db.Column(db.String(80), nullable=False, default="")
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_online(self):
        seen = self.last_seen_at
        if seen is None:
            return False
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        return utcnow() - seen < ONLINE_WINDOW

    def public(self, relation="none"):
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "bio": self.bio,
            "status_text": self.status_text,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "online": self.is_online(),
            "relation": relation,
        }


class FriendRequest(db.Model):
    __tablename__ = "friend_requests"

    id = db.Column(db.String(32), primary_key=True, default=new_id)
    from_user_id = db.Column(db.String(32), db.ForeignKey("users.id"), nullable=False, index=True)
    to_user_id = db.Column(db.String(32), db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (db.UniqueConstraint("from_user_id", "to_user_id"),)


class Friendship(db.Model):
    __tablename__ = "friendships"

    user_a = db.Column(db.String(32), primary_key=True)
    user_b = db.Column(db.String(32), primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Block(db.Model):
    __tablename__ = "blocks"

    blocker_id = db.Column(db.String(32), primary_key=True)
    blocked_id = db.Column(db.String(32), primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.String(32), primary_key=True, default=new_id)
    user_a = db.Column(db.String(32), nullable=False)
    user_b = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (db.UniqueConstraint("user_a", "user_b"),)

    def other_id(self, user_id):
        return self.user_b if self.user_a == user_id else self.user_a


class ConversationMember(db.Model):
    __tablename__ = "conversation_members"

    conversation_id = db.Column(db.String(32), primary_key=True)
    user_id = db.Column(db.String(32), primary_key=True)
    last_read_at = db.Column(db.DateTime(timezone=True))
    typing_until = db.Column(db.DateTime(timezone=True))
    hidden = db.Column(db.Boolean, nullable=False, default=False)


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.String(32), primary_key=True, default=new_id)
    conversation_id = db.Column(db.String(32), nullable=False, index=True)
    sender_id = db.Column(db.String(32), nullable=False)
    body = db.Column(db.Text, nullable=False)
    reply_to_id = db.Column(db.String(32))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_for_everyone = db.Column(db.Boolean, nullable=False, default=False)


class MessageHide(db.Model):
    __tablename__ = "message_hides"

    message_id = db.Column(db.String(32), primary_key=True)
    user_id = db.Column(db.String(32), primary_key=True)


def touch_user(user):
    user.last_seen_at = utcnow()


def are_friends(a, b):
    lo, hi = pair(a, b)
    return Friendship.query.filter_by(user_a=lo, user_b=hi).first() is not None


def blocked_by(blocker_id, blocked_id):
    return Block.query.filter_by(blocker_id=blocker_id, blocked_id=blocked_id).first() is not None


def blocked_either_way(a, b):
    return (
        Block.query.filter(
            or_(
                and_(Block.blocker_id == a, Block.blocked_id == b),
                and_(Block.blocker_id == b, Block.blocked_id == a),
            )
        ).first()
        is not None
    )


def relation_of(me, other):
    if me == other:
        return "self"
    if blocked_by(me, other):
        return "blocked"
    if blocked_by(other, me):
        return "none"
    if are_friends(me, other):
        return "friends"
    if FriendRequest.query.filter_by(from_user_id=other, to_user_id=me).first():
        return "incoming"
    if FriendRequest.query.filter_by(from_user_id=me, to_user_id=other).first():
        return "outgoing"
    return "none"


def get_or_create_conversation(a, b):
    lo, hi = pair(a, b)
    convo = Conversation.query.filter_by(user_a=lo, user_b=hi).first()
    if convo:
        return convo
    convo = Conversation(id=new_id(), user_a=lo, user_b=hi)
    db.session.add(convo)
    db.session.add(ConversationMember(conversation_id=convo.id, user_id=a))
    db.session.add(ConversationMember(conversation_id=convo.id, user_id=b))
    db.session.commit()
    return convo


def can_chat(me, other):
    return are_friends(me, other) and not blocked_either_way(me, other)


def message_payload(msg, me_id, other_last_read, delivered):
    body = "" if msg.deleted_for_everyone else msg.body
    status = "sent"
    if msg.sender_id == me_id:
        created = msg.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if other_last_read and created and other_last_read >= created:
            status = "read"
        elif delivered:
            status = "delivered"
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "sender_id": msg.sender_id,
        "body": body,
        "reply_to_id": msg.reply_to_id,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "deleted": bool(msg.deleted_for_everyone),
        "mine": msg.sender_id == me_id,
        "status": status,
    }
