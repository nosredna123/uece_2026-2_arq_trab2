import logging
import sqlite3
import sys

from pika.exceptions import AMQPError

from notification_mock.settings import WorkerSettings
from notification_mock.store import NotificationStore
from notification_mock.worker import create_connection


LOGGER = logging.getLogger(__name__)


def main() -> int:
    try:
        settings = WorkerSettings.from_environment()
        store = NotificationStore(settings.store_path)
        store.count_retryable()
        connection = create_connection(settings)
        connection.close()
    except (AMQPError, OSError, RuntimeError, sqlite3.Error) as error:
        LOGGER.error(
            "Notification health check failed error_type=%s",
            type(error).__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    sys.exit(main())
