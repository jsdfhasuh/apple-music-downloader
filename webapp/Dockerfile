FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
  apt-get install -y --no-install-recommends docker.io ffmpeg flac && \
  rm -rf /var/lib/apt/lists/*

COPY webapp/requirements.txt /app/webapp/requirements.txt
RUN pip install --no-cache-dir -r /app/webapp/requirements.txt

COPY config.yaml /app/config.yaml
COPY webapp /app/webapp
COPY tools /app/tools

RUN chmod +x /app/webapp/start.sh

EXPOSE 5000

CMD ["bash", "webapp/start.sh"]
