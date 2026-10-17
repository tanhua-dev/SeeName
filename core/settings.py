"""Cached application settings and their persistent JSON representation."""
from __future__ import annotations

import io
import json
from .defaults import (
    DEFAULT_PORT,
    MODEL,
)
from .paths import (
    CONFIG_PATH,
)


_CFG: dict | None = None


def config() -> dict:
    """读取软件目录下的 config.json，缺项用默认值补。"""
    global _CFG
    if _CFG is None:
        d = {'port': DEFAULT_PORT, 'models_dir': '', 'model': MODEL}
        try:
            with io.open(CONFIG_PATH, encoding='utf-8') as f:
                d.update(json.load(f) or {})
        except Exception:
            pass
        _CFG = d
    return _CFG


def save_config(**kw) -> dict:
    c = config()
    c.update(kw)
    try:
        with io.open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return c
