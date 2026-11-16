FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Keep the same sibling path used by the shared CSS import in local builds.
COPY static/team-theme.css /build/static/team-theme.css
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
COPY requirements.txt requirements-production.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt \
    && groupadd --gid 10001 teamapp \
    && useradd --create-home --uid 10001 --gid 10001 --shell /bin/sh teamapp \
    && install -d -m 0700 -o teamapp -g teamapp /data /home/teamapp/.ssh
COPY --chown=teamapp:teamapp . ./
COPY --chown=teamapp:teamapp --from=frontend /build/frontend/dist ./frontend/dist
ENV KAKAO_PRACTICE_DB=/data/basic.sqlite3 TEAM_EDUCATION_DB=/data/education.sqlite3
# The entrypoint fixes only the mounted data paths, then drops to UID/GID 10001.
USER root
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/education/health',timeout=3).close()"
ENTRYPOINT ["python", "/app/deployment_entrypoint.py"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
