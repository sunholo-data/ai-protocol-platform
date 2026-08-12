FROM python:3.12-slim

# Install uv
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ADD https://astral.sh/uv/0.5.24/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"

WORKDIR /app

COPY . .

# Cloud Run environment
ARG FIREBASE_BUCKET=""
ARG GOOGLE_CLOUD_PROJECT=""
ARG GOOGLE_CLOUD_LOCATION=""
ARG COMMIT_SHA=""

ENV FIREBASE_BUCKET=${FIREBASE_BUCKET}
ENV GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}
ENV GOOGLE_CLOUD_LOCATION=${GOOGLE_CLOUD_LOCATION}
ENV GOOGLE_GENAI_USE_VERTEXAI=true
ENV GOOGLE_CLOUD_LOGGING=1
ENV COMMIT_SHA=${COMMIT_SHA}

RUN uv sync --frozen --no-install-project

EXPOSE 8080

# Dual-role image:
#   - as standalone ingress (platform-backend service): Cloud Run injects
#     PORT=8080, so we listen on 8080.
#   - as sidecar of platform-frontend: Cloud Run does NOT inject PORT into
#     sidecars, so ${PORT:-1956} falls through to 1956 — which is what the
#     frontend's proxy (BACKEND_URL=http://localhost:1956) expects.
# Do not "simplify" this to a hardcoded port — it would break one of the roles.
CMD ["sh", "-c", "uv run uvicorn fast_api_app:app --host 0.0.0.0 --port ${PORT:-1956} --workers 1"]
