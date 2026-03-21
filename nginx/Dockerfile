FROM nginx:1.27-alpine
RUN apk add --no-cache apache2-utils
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx/docker-entrypoint.sh /docker-entrypoint-custom.sh
RUN chmod +x /docker-entrypoint-custom.sh
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint-custom.sh"]
