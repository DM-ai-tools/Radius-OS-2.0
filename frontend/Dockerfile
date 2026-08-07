# Production SPA (optional separate Railway service).
# Preferred: deploy the repo-root Dockerfile (API + SPA together).

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY start-frontend.sh /start-frontend.sh
RUN chmod +x /start-frontend.sh && sed -i 's/\r$//' /start-frontend.sh
ENV PORT=8080
EXPOSE 8080
CMD ["/start-frontend.sh"]
