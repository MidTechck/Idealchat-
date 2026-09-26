# Idealchat

Private 1-to-1 messenger. Flask + Socket.IO.

Create a free account, search a username, send a friend request, and chat only after they accept. Block hides that person and stops their messages.

## Railway

1. New project from GitHub repo `MidTechck/Idealchat-`
2. Add a **PostgreSQL** plugin (recommended)
3. Variables:
   - `SECRET_KEY` = a long random string
   - `DATABASE_URL` is set automatically by the Postgres plugin
4. Start command must be:

```
python server.py
```

Do not use a Node start command. This app is Flask.

## Local / Termux

```
pkg install python
pip install -r requirements.txt
python server.py
```

Opens on port 2009 unless `PORT` is set.

## Files

- `app.py` routes
- `database.py` users, friends, blocks, messages
- `server.py` starts the real-time server
- `templates/login.html`
- `templates/Signin.html`
- `templates/Chat.html`
- `templates/Profile.html`
