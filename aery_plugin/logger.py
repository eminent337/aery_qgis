import logging
import os

try:
    from qgis.core import QgsMessageLog, Qgis
    _HAS_QGIS = True
except ImportError:
    _HAS_QGIS = False

# Global lock to ensure thread-safe logger initialization
import threading
_logger_lock = threading.Lock()
_logger_initialized = False

class AeryLogger:
    """Wrapper for Python logging and QGIS Message Log."""
    def __init__(self):
        self.logger = logging.getLogger("aery")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[Aery] %(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        
        level_str = os.environ.get("AERY_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)
        self.logger.setLevel(level)

    def _log_qgis(self, message: str, level: int):
        if _HAS_QGIS:
            try:
                QgsMessageLog.logMessage(message, "Aery AI", level)
            except Exception:
                pass

    def debug(self, *args, **kwargs):
        """Debug logging with variable args (compatible with %% formatting and exc_info)."""
        self.logger.debug(*args, **kwargs)
        msg = str(args[0]) if args else ""
        if _HAS_QGIS and os.environ.get("AERY_LOG_LEVEL", "").upper() == "DEBUG":
            self._log_qgis(msg, getattr(Qgis.MessageLevel, "Info", 0))

    def info(self, *args, **kwargs):
        """Info logging with variable args."""
        self.logger.info(*args, **kwargs)
        msg = str(args[0]) if args else ""
        if _HAS_QGIS:
            self._log_qgis(msg, getattr(Qgis.MessageLevel, "Info", 0))
    def warning(self, *args, **kwargs):
        """Warning logging with variable args."""
        self.logger.warning(*args, **kwargs)
        msg = str(args[0]) if args else ""
        if _HAS_QGIS:
            self._log_qgis(msg, getattr(Qgis.MessageLevel, "Warning", 1))
    def error(self, *args, **kwargs):
        """Error logging with variable args (compatible with exc_info)."""
        self.logger.error(*args, **kwargs)
        msg = str(args[0]) if args else ""
        if _HAS_QGIS:
            self._log_qgis(msg, getattr(Qgis.MessageLevel, "Critical", 2))

    def exception(self, *args, **kwargs):
        """Error logging with automatic exception info."""
        kwargs.setdefault("exc_info", True)
        self.error(*args, **kwargs)
# Global instance
_logger_instance = None

def get_logger() -> AeryLogger:
    global _logger_instance
    with _logger_lock:
        if _logger_instance is None:
            _logger_instance = AeryLogger()
    return _logger_instance

logger = get_logger()
