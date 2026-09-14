# The React UI (spec §39), built and served as static files.
FROM node:24-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
# As a TEMPLATE: the image renders /etc/nginx/templates/*.template into conf.d at
# start, substituting only variables that are set - so nginx's own $host is safe.
ENV API_UPSTREAM=api:8000
COPY docker/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
