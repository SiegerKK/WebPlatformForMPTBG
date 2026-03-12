"""
Worker process entry point.
Handles background tasks: turn timeouts, bot fallbacks, Temporal workflows.
"""
import logging
import signal
import sys

logger = logging.getLogger(__name__)

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Worker starting...")

    # Graceful shutdown handler
    def _shutdown(signum, frame):
        logger.info("Worker shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("Worker ready. Waiting for tasks...")

    # TODO: connect to Temporal and start polling
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Worker stopped.")

if __name__ == "__main__":
    main()
