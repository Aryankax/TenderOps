"""
Observability adapter for neatlogs.
Since we cannot verify the neatlogs API immediately, we provide an isolated adapter.
"""
import logging

logger = logging.getLogger("tenderops")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

class Observability:
    @staticmethod
    def log_event(event_name: str, **kwargs):
        """Log an observability event."""
        # TODO: Implement actual neatlogs integration once API is verified.
        logger.info(f"Event: {event_name} | {kwargs}")

    @staticmethod
    def log_error(error_name: str, error: Exception, **kwargs):
        logger.error(f"Error: {error_name} | {error} | {kwargs}")

observability = Observability()
