FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html app.js style.css /usr/share/nginx/html/
RUN chmod 644 /usr/share/nginx/html/index.html /usr/share/nginx/html/app.js /usr/share/nginx/html/style.css
