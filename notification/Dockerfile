FROM python:3.12-slim
RUN groupadd -r notification && useradd -r -g notification -d /app -s /sbin/nologin notification
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY notification_app.py ./
RUN chown -R notification:notification /app
USER notification
EXPOSE 9004
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9004/health')" || exit 1
CMD ["gunicorn", "--bind", "0.0.0.0:9004", "--workers", "2", "--timeout", "30", "notification_app:app"]
