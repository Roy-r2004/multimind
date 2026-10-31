# Restart-only gateway for production: holds the Docker socket itself and
# allows nothing but listing containers and restarting this deployment's gosom
# (see templates/nginx.conf.template). Rules are baked in (no bind mount from
# the repo, which Coolify does not provide).
FROM nginx:1.27-alpine
ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx
RUN rm -f /etc/nginx/conf.d/default.conf
COPY templates/ /etc/nginx/templates/
