import logging
import logging.config
from pathlib import Path


def setup_logging():
    # Path to logging configuration file
    # .parent.parent in case of this file will be moved
    log_config_path = Path(__file__).parent.parent.parent / "logging.conf"

    logging.config.fileConfig(
        log_config_path,
        disable_existing_loggers=False,
        defaults={'client_addr': '-'}  # Default value for access logs
    )

    # Capture warnings from the warnings module
    logging.captureWarnings(True)