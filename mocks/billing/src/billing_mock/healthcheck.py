import logging
import sqlite3
import sys

from pika.exceptions import AMQPError

from billing_mock.observability import configure_logging
from billing_mock.settings import WorkerSettings
from billing_mock.store import BillingStore
from billing_mock.worker import create_connection


LOGGER = logging.getLogger(__name__)


def main() -> int:
    try:
        settings = WorkerSettings.from_environment()
        store = BillingStore(settings.store_path)
        store.count_unpublished()
        connection = create_connection(settings)
        connection.close()
    except (AMQPError, OSError, RuntimeError, sqlite3.Error) as error:
        LOGGER.error(
            "health_check.failed",
            extra={"error_type": type(error).__name__},
        )
        return 1
    return 0


if __name__ == "__main__":
    configure_logging()
    sys.exit(main())
