# Build offline from the deployment's existing Python 3.12 base image.
# Record the base RepoDigest and resulting image ID in the deployment evidence.
FROM python:3.12-slim
COPY wheels /wheels
RUN pip install --no-index --find-links=/wheels pytest pytest-asyncio fastapi httpx pydantic pydantic-settings pypdf tenacity && rm -rf /wheels
