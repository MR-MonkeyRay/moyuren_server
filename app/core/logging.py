"""日志配置模块。"""

import logging
import sys
from pathlib import Path

from app.core.config import LoggingConfig


def setup_logging(config: LoggingConfig, logger_name: str | None = None) -> logging.Logger:
    """
    配置日志：控制台输出必选，文件输出可选。

    Args:
        config: 日志配置。
        logger_name: logger 名称；为 None 时返回 root logger。

    Returns:
        logging.Logger: 配置好的 logger 实例。
    """
    logger = logging.getLogger(logger_name)

    # 清空已有 handler
    logger.handlers.clear()
    logger.setLevel(getattr(logging, config.level, logging.INFO))

    # 不向 root logger 传播，避免日志重复输出
    logger.propagate = False

    # 创建日志格式器
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台 handler（始终启用）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, config.level, logging.INFO))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler（仅在配置了文件路径时启用）
    if config.file:
        log_file_path = Path(config.file)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(getattr(logging, config.level, logging.INFO))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # 让 Uvicorn 日志使用相同格式
    uvicorn_loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    for uvicorn_logger_name in uvicorn_loggers:
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.setLevel(getattr(logging, config.level, logging.INFO))
        uvicorn_logger.propagate = False

        # 添加控制台 handler
        uvicorn_console_handler = logging.StreamHandler(sys.stdout)
        uvicorn_console_handler.setLevel(getattr(logging, config.level, logging.INFO))
        uvicorn_console_handler.setFormatter(formatter)
        uvicorn_logger.addHandler(uvicorn_console_handler)

        # 配置了日志文件时同步添加文件 handler
        if config.file:
            uvicorn_file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            uvicorn_file_handler.setLevel(getattr(logging, config.level, logging.INFO))
            uvicorn_file_handler.setFormatter(formatter)
            uvicorn_logger.addHandler(uvicorn_file_handler)

    return logger
