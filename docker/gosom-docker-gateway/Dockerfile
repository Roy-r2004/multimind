# Restart-only allowlist gateway in front of docker-socket-proxy, with its
# rules baked in (no bind mount from the repo, which Coolify does not provide).
FROM nginx:1.27-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
COPY templates/ /etc/nginx/templates/
