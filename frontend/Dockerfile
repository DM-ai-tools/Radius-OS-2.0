# Production SPA (optional separate Railway service).
# Preferred: deploy the repo-root Dockerfile (API + SPA together).

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Set at Railway build time: VITE_API_URL=https://<your-api>.up.railway.app
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
ENV PORT=8080
EXPOSE 8080
# Rewrite listen port to Railway's $PORT, then start nginx
CMD ["sh", "-c", "sed -i \"s/listen 8080;/listen ${PORT};/g\" /etc/nginx/conf.d/default.conf && exec nginx -g 'daemon off;'"]
