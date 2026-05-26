FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends xmlsec1 && rm -rf /var/lib/apt/lists/*
RUN groupadd -r dimed && useradd -r -g dimed -d /app -s /sbin/nologin dimed
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R dimed:dimed /app
USER dimed
EXPOSE 9009
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9009/health')" || exit 1
CMD ["gunicorn", "--bind", "0.0.0.0:9009", "--workers", "2", "--timeout", "120", "billing_app:app"]
