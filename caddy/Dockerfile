# Imagen de Caddy del proyecto.
#
# El pack original usa directamente la imagen oficial `caddy:2-alpine`; este
# Dockerfile existe para poder construir una imagen propia si algun dia hace
# falta fijar version o anadir modulos (xcaddy). Hoy no cambia nada: es la
# misma imagen base.
FROM caddy:2-alpine

# Version fijada por el pack (caddy:2-alpine). Para modulos extra, cambiar a:
#   FROM caddy:2-builder AS builder
#   RUN xcaddy build --with github.com/...
#   FROM caddy:2-alpine
#   COPY --from=builder /usr/bin/caddy /usr/bin/caddy
