# nebula_core/utils/logger.py
import logging
import sys
from pathlib import Path

def setup_logger(name: str):
    """Создаёт настроенный логгер для ядра."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Избегаем дублирования хэндлеров
    if logger.hasHandlers():
        logger.handlers.clear()

    # Формат логов
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Вывод в консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Логи в файл
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

def get_logger(name: str) -> logging.Logger:
    """Возвращает логгер с заданным именем."""
    return setup_logger(name)
