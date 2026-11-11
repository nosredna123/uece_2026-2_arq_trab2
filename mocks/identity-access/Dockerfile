FROM python:3.12.9-slim-bookworm AS dependencies
WORKDIR /workspace
COPY pyproject.toml constraints.txt ./
COPY src src
COPY scenario-participants.json scenario-participants.json
RUN pip wheel --no-cache-dir -c constraints.txt --wheel-dir /wheels .

FROM dependencies AS test
RUN pip install --no-cache-dir -c constraints.txt ".[test]"
COPY tests/conftest.py tests/test_identity_runtime.py tests/test_scenario_identities.py tests/
RUN python -m pytest -x --import-mode=importlib
CMD ["python", "-m", "pytest", "-x", "--import-mode=importlib"]

FROM python:3.12.9-slim-bookworm AS runtime
WORKDIR /app
COPY --from=dependencies /workspace/scenario-participants.json /app/scenario-participants.json
COPY --from=dependencies /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels eventhub-identity-access-mock==0.1.0 && rm -r /wheels
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "identity_access_mock.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
