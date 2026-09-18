FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /data
ENV DATA_DIR=/data PORT=3033
EXPOSE 3033
CMD ["sh","-c","gunicorn -b 0.0.0.0:${PORT:-3033} --workers 2 --threads 4 app.main:app"]
