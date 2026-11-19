FROM caddy:2-alpine AS caddy-bin

FROM python:3.12-alpine

COPY --from=caddy-bin /usr/bin/caddy /usr/local/bin/caddy
RUN caddy version \
 && apk add --no-cache ca-certificates tzdata

WORKDIR /app
ARG DNX_COMMIT=""
RUN printf '%s' "$DNX_COMMIT" > /app/COMMIT
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# La base de datos vive en /data (volumen). Debe pertenecer al uid del servicio.
RUN mkdir -p /data && chown -R 1000:1000 /data

COPY app.py db.py security.py caddyops.py monitor.py integrations.py updater.py ./
COPY templates ./templates
COPY static ./static

EXPOSE 9095
ENTRYPOINT ["python", "app.py"]
CMD ["--serve"]
