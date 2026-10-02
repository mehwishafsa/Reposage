# RepoSage web app: one service = FastAPI API + the built React frontend.
# Used by Render (render.yaml), but works anywhere Docker runs:
#   docker build -t reposage . && docker run -p 8000:8000 -e GEMINI_API_KEY=... reposage

# ---- 1. build the React frontend ----
FROM node:22-slim AS web
WORKDIR /app/webapp/frontend
COPY webapp/frontend/package.json webapp/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY webapp/frontend/ ./
RUN npm run build

# ---- 2. the Python server ----
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REPOSAGE_DATA=/tmp/reposage-data PORT=8000
WORKDIR /app
COPY webapp/backend/requirements.txt webapp/backend/requirements.txt
RUN pip install --no-cache-dir -r webapp/backend/requirements.txt
# the analysis engine (shared with the Claude Code plugin) and the web app
COPY reposage/ reposage/
COPY webapp/backend/app/ webapp/backend/app/
COPY webapp/samples/ webapp/samples/
COPY --from=web /app/webapp/frontend/dist/ webapp/frontend/dist/
# run as a normal user: uploaded code is only read, but least privilege anyway
RUN useradd --create-home reposage && mkdir -p /tmp/reposage-data && chown reposage /tmp/reposage-data
USER reposage
WORKDIR /app/webapp/backend
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
