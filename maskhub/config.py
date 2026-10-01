"""Runtime settings for the gateway process."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_DATA_DIR = "data"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


@dataclass(frozen=True)
class Settings:
    data_dir: str
    host: str
    port: int


def parse_listen_address(raw: str) -> tuple[str, int]:
    text = raw.strip()
    if not text:
        raise ValueError("listen address is empty")
    if text.isdigit():
        return DEFAULT_HOST, _coerce_port(text)
    if ":" not in text:
        return text, DEFAULT_PORT
    host, _, port_text = text.rpartition(":")
    return (host.strip() or DEFAULT_HOST), _coerce_port(port_text)


def _coerce_port(text: str) -> int:
    try:
        port = int(text)
    except ValueError as exc:
        raise ValueError(f"invalid port: {text!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    data_dir = env.get("MASKHUB_DATA_DIR", "").strip() or DEFAULT_DATA_DIR
    address = env.get("MASKHUB_ADDR", "").strip()
    host, port = parse_listen_address(address) if address else (DEFAULT_HOST, DEFAULT_PORT)
    return Settings(data_dir=data_dir, host=host, port=port)
