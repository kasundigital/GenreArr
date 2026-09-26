FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /data
ENV DATA_DIR=/data PORT=3033 PUID=1000 PGID=1000 PYTHONUNBUFFERED=1
EXPOSE 3033
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health'%os.getenv('PORT','3033'),timeout=4)" || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
# One worker: jobs, webhooks and the scheduler share in-process locks. Threads handle concurrency.
CMD ["sh","-c","exec gunicorn -b 0.0.0.0:${PORT:-3033} --workers 1 --threads 8 --timeout 300 app.main:app"]
