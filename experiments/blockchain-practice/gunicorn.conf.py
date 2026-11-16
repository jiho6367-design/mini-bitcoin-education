"""One process owns the classroom scheduler and in-memory active sessions."""
import os
import signal

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = 1
worker_class = "gthread"
threads = 64
timeout = 120
graceful_timeout = 30
preload_app = False
accesslog = "-"
errorlog = "-"


def post_worker_init(worker):
    """Release live WebSocket requests before Gunicorn waits for them to drain."""
    app = worker.wsgi
    runtime = app.extensions.get("education")
    previous = signal.getsignal(signal.SIGTERM)

    def stop(sig, frame):
        if runtime is not None:
            runtime.stop_event.set()
        if callable(previous):
            previous(sig, frame)
        else:
            worker.handle_exit(sig, frame)

    signal.signal(signal.SIGTERM, stop)


def worker_exit(_server, worker):
    # Boot failures may happen before an application was loaded. Do not import a
    # second global app just to clean up an application that never started.
    app = getattr(worker, "wsgi", None)
    if app is None:
        return
    runtime = app.extensions.get("education")
    if runtime is not None:
        runtime.close()
    store = app.extensions.get("practice_store")
    if store is not None:
        store.close()
