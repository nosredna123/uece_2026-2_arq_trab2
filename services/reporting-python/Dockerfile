FROM python:3.12.9-slim-bookworm AS dependencies
WORKDIR /workspace
COPY pyproject.toml .
COPY constraints.txt .
COPY src src
RUN pip wheel --no-cache-dir -c constraints.txt --wheel-dir /wheels .

FROM dependencies AS test
RUN pip install --no-cache-dir -c constraints.txt ".[test]"
COPY migrations migrations
COPY alembic.ini .
COPY tests tests
RUN python -m pytest -x --import-mode=importlib
CMD ["python", "-m", "pytest", "-x", "--import-mode=importlib"]

FROM python:3.12.9-slim-bookworm AS runtime
WORKDIR /app
COPY --from=dependencies /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -r /wheels
COPY migrations migrations
COPY alembic.ini .
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn reporting_service.app:create_app --factory --host 0.0.0.0 --port 8080"]
