# Production SPA (optional separate Railway service). Root Dockerfile is preferred.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Point at the public API URL at build time when deploying frontend separately
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Railway injects PORT — nginx template listens on it
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "sed -i \"s/listen 8080;/listen ${PORT};/\" /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
