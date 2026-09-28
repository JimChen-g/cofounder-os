# Observed on Spark 2026-09-28; preserve the existing deployed image until separately rebuilt.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc
COPY wheels /wheels
COPY engineering-tests.lock /engineering-tests.lock
RUN pip install --no-index --find-links=/wheels -r /engineering-tests.lock && rm -rf /wheels
