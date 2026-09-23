"""Logging helpers shared by the CLI, GUI, and council runtime."""

import logging
import sys


class DynamicStdoutHandler(logging.Handler):
    """Write formatted records to the current stdout stream."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stderr if record.levelno >= logging.WARNING else sys.stdout
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)


_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Return a project logger with plain-message console output."""
    global _CONFIGURED
    logger = logging.getLogger(f"agentcouncil.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = True
    if not _CONFIGURED:
        handler = DynamicStdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        root = logging.getLogger("agentcouncil")
        root.setLevel(logging.INFO)
        root.propagate = False
        root.addHandler(handler)
        _CONFIGURED = True
    return logger
