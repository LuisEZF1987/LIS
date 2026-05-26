FROM python:3.12-slim
RUN groupadd -r healthit && useradd -r -g healthit -d /app -s /sbin/nologin healthit
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lis_app.py ./
RUN chown -R healthit:healthit /app
USER healthit
EXPOSE 9008
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9008/health')" || exit 1
CMD ["gunicorn", "--bind", "0.0.0.0:9008", "--workers", "2", "--timeout", "120", "lis_app:app"]
