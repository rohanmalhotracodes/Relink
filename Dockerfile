FROM node:22-alpine AS web-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPO_ROOT=/app \
    PORT=8002

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY demo-jaffle/ ./demo-jaffle/
COPY demo-warehouse/ ./demo-warehouse/
COPY --from=web-build /build/web/dist ./web/dist

RUN pip install --no-cache-dir .

EXPOSE 8002
CMD ["sh", "scripts/start_production.sh"]
