"""One process owns the classroom scheduler and in-memory active sessions."""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = 1
worker_class = "gthread"
threads = 64
timeout = 120
graceful_timeout = 30
preload_app = False
accesslog = "-"
errorlog = "-"


def worker_exit(_server, _worker):
    from app import app
    runtime = app.extensions.get("education")
    if runtime is not None:
        runtime.close()
    app.extensions["practice_store"].close()
