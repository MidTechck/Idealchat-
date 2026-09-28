import os

from app import app, socketio


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "2009"))
    print("Idealchat listening on 0.0.0.0:%s" % port, flush=True)
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
