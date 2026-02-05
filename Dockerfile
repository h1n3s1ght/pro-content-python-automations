FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x scripts/*.sh

# Create simple command aliases so Render can run `web`, `worker`, `beat`, etc.
RUN ln -sf /app/scripts/entrypoint.sh /usr/local/bin/web \
  && ln -sf /app/scripts/entrypoint.sh /usr/local/bin/worker \
  && ln -sf /app/scripts/entrypoint.sh /usr/local/bin/beat \
  && ln -sf /app/scripts/entrypoint.sh /usr/local/bin/migrate \
  && ln -sf /app/scripts/entrypoint.sh /usr/local/bin/call

EXPOSE 8010

ENTRYPOINT ["./scripts/entrypoint.sh"]
CMD ["web"]
