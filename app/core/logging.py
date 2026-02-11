"""Logging configuration module."""

import logging
import sys
from pathlib import Path

from app.core.config import LoggingConfig


def setup_logging(config: LoggingConfig, logger_name: str | None = None) -> logging.Logger:
    """
    Configure logging with console handler and optional file handler.

    Args:
        config: Logging configuration.
        logger_name: Name for the logger. If None, returns root logger.

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(logger_name)

    # Clear existing handlers
    logger.handlers.clear()
    logger.setLevel(getattr(logging, config.level, logging.INFO))

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False

    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, config.level, logging.INFO))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (only if file path is specified)
    if config.file:
        log_file_path = Path(config.file)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(getattr(logging, config.level, logging.INFO))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Configure Uvicorn loggers to use the same format
    uvicorn_loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for uvicorn_logger_name in uvicorn_loggers:
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.setLevel(getattr(logging, config.level, logging.INFO))
        uvicorn_logger.propagate = False

        # Add console handler
        uvicorn_console_handler = logging.StreamHandler(sys.stdout)
        uvicorn_console_handler.setLevel(getattr(logging, config.level, logging.INFO))
        uvicorn_console_handler.setFormatter(formatter)
        uvicorn_logger.addHandler(uvicorn_console_handler)

        # Add file handler if configured
        if config.file:
            uvicorn_file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            uvicorn_file_handler.setLevel(getattr(logging, config.level, logging.INFO))
            uvicorn_file_handler.setFormatter(formatter)
            uvicorn_logger.addHandler(uvicorn_file_handler)

    return logger
