FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
COPY requirements.txt requirements-production.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt \
    && useradd --create-home --uid 10001 teamapp \
    && mkdir /data && chown teamapp:teamapp /data
COPY --chown=teamapp:teamapp . ./
COPY --chown=teamapp:teamapp --from=frontend /build/dist ./frontend/dist
ENV KAKAO_PRACTICE_DB=/data/basic.sqlite3 TEAM_EDUCATION_DB=/data/education.sqlite3
USER teamapp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/education/health',timeout=3)"
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
