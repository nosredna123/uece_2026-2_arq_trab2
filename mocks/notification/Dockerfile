FROM python:3.12.9-slim-bookworm AS dependencies
WORKDIR /workspace
COPY pyproject.toml constraints.txt ./
COPY src src
RUN pip wheel --no-cache-dir -c constraints.txt --wheel-dir /wheels .

FROM dependencies AS test
RUN pip install --no-cache-dir -c constraints.txt ".[test]"
COPY tests/conftest.py tests/test_notification_processor.py tests/
RUN python -m pytest -x --import-mode=importlib
CMD ["python", "-m", "pytest", "-x", "--import-mode=importlib"]

FROM python:3.12.9-slim-bookworm AS runtime
WORKDIR /app
COPY --from=dependencies /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels eventhub-notification-mock==0.1.0 && rm -r /wheels
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["python", "-m", "notification_mock.healthcheck"]
CMD ["python", "-m", "notification_mock.worker"]
