"""Process entry point."""

from __future__ import annotations

import logging
import threading

from maskhub.config import Settings, load_settings
from maskhub.console.routes import build_router
from maskhub.console.runtime import build_runtime
from maskhub.console.server import ConsoleServer
from maskhub.store.kvstore import RecordStore

LOGGER = logging.getLogger("maskhub")


def create_server(settings: Settings | None = None) -> ConsoleServer:
    resolved = settings if settings is not None else load_settings()
    store = RecordStore(resolved.data_dir)
    store.load()
    runtime = build_runtime(store)
    return ConsoleServer(build_router(runtime), resolved.host, resolved.port)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = create_server()
    server.start()
    LOGGER.info("listening on %s", server.url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        LOGGER.info("interrupted, shutting down")
    finally:
        server.stop()
    return 0
