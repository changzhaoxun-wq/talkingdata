FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS cli
RUN npm install --global opencode-ai@1.2.27

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=cli /usr/local/bin/node /usr/local/bin/node
COPY --from=cli /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/opencode-ai/bin/opencode /usr/local/bin/opencode \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 agent \
    && mkdir -p /data && chown agent:agent /data
COPY app ./app
COPY web ./web
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER agent
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

FROM runtime AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY --chown=agent:agent tests ./tests
COPY pytest.ini .
USER agent
CMD ["python", "-m", "pytest", "-q", "tests"]

FROM runtime AS production
