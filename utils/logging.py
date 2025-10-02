import logging
import sys
import time
from typing import Any, Dict
import orjson


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "time": int(time.time() * 1000),
            "message": record.getMessage(),
        }
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            payload.update(record.extra)
        return orjson.dumps(payload).decode("utf-8")


def get_logger(name: str = "jobs") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: int, message: str, **kwargs: Any) -> None:
    logger.log(level, message, extra={"extra": kwargs})

