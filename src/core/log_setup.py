import logging
import sys

from src.core.config import settings


def setup_logging(name: str) -> logging.Logger:
    """
    Configures and returns a logger with the specified name.
    Uses structured-like formatting suitable for production logs.
    """
    logger = logging.getLogger(name)
    logger.setLevel(settings.log_level.upper())
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger
