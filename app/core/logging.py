import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
MAX_BYTES = 5_000_000
BACKUP_COUNT = 5


def setup_logging(app_name: str, log_dir: str = "logs", debug: bool = False) -> None:
    Path(log_dir).mkdir(exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    all_logs = RotatingFileHandler(Path(log_dir) / f"{app_name}.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    all_logs.setFormatter(formatter)

    error_logs = RotatingFileHandler(
        Path(log_dir) / f"{app_name}_errors.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    error_logs.setLevel(logging.ERROR)
    error_logs.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.handlers = [console, all_logs, error_logs]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
