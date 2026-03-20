"""
Structured logging setup for P2P file sharing system.

Provides consistent, structured logging across all modules.
Uses Python's standard logging module - no external dependencies.

ANDROID COMPATIBILITY:
- Uses only standard library logging
- Log output can be redirected to Android's Logcat
- No file-based logging by default (Android manages its own logs)
"""

import logging
import sys
from typing import Optional


# Log format with timestamp, level, module, and message
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Module-specific loggers namespace
LOGGER_PREFIX = "p2p"


def setup_logging(
    level: int = logging.INFO,
    log_format: Optional[str] = None,
    date_format: Optional[str] = None,
    stream: Optional[object] = None
) -> None:
    """
    Configure the root logger for the P2P system.
    
    Should be called once at application startup (in CLI or API entry point).
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Custom log format string (optional)
        date_format: Custom date format string (optional)
        stream: Output stream (defaults to sys.stderr)
        
    Example:
        # In CLI main.py
        from utils.logger import setup_logging
        setup_logging(level=logging.DEBUG)
    """
    formatter = logging.Formatter(
        fmt=log_format or DEFAULT_FORMAT,
        datefmt=date_format or DEFAULT_DATE_FORMAT
    )
    
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(formatter)
    
    # Configure the root p2p logger
    root_logger = logging.getLogger(LOGGER_PREFIX)
    root_logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    
    # Prevent propagation to root logger
    root_logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.
    
    Args:
        name: Module name (e.g., "tracker", "peer.downloader")
        
    Returns:
        Logger instance with the p2p prefix
        
    Example:
        # In tracker_server.py
        from utils.logger import get_logger
        logger = get_logger("tracker.server")
        logger.info("Server started on port %d", port)
    """
    return logging.getLogger(f"{LOGGER_PREFIX}.{name}")


class LoggerMixin:
    """
    Mixin class to add a logger property to any class.
    
    Example:
        class MyClass(LoggerMixin):
            def do_something(self):
                self.logger.info("Doing something")
    """
    
    @property
    def logger(self) -> logging.Logger:
        """Get a logger named after this class."""
        return get_logger(self.__class__.__name__)


# =============================================================================
# Testing Support
# =============================================================================

if __name__ == "__main__":
    # Demonstrate logging setup
    setup_logging(level=logging.DEBUG)
    
    # Get loggers for different modules
    tracker_log = get_logger("tracker")
    peer_log = get_logger("peer")
    downloader_log = get_logger("peer.downloader")
    
    print("Logging Demo")
    print("=" * 60)
    
    tracker_log.debug("Debug message from tracker")
    tracker_log.info("Info message from tracker")
    peer_log.warning("Warning message from peer")
    downloader_log.error("Error message from downloader")
    
    # Demonstrate LoggerMixin
    class TestClass(LoggerMixin):
        def test_method(self):
            self.logger.info("Message from mixin class")
    
    obj = TestClass()
    obj.test_method()
    
    print()
    print("Logging setup complete!")
