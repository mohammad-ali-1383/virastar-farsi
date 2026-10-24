# Production image for the Persian proofreading frontend.
#
# Stage 1 compiles the static bundle with Vite; stage 2 serves only those files
# with Nginx. Node and the source tree never ship in the final image.

# --- Stage 1: build the static bundle --------------------------------------- #
FROM node:24-alpine AS builder

WORKDIR /app

# Copy only the manifests first so the dependency layer stays cached until the
# lockfile or package.json changes.
COPY package.json package-lock.json ./
RUN npm ci

# Public, build-time configuration. Vite inlines every VITE_* value into the
# bundle, so this must never hold a secret. The default "/" makes the browser
# call the API on the same origin, where Nginx proxies /api/ to the backend.
ARG VITE_API_BASE_URL=/
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY . .

# `npm run build` is `tsc --noEmit && vite build`; it writes /app/dist.
RUN npm run build

# --- Stage 2: serve the static files --------------------------------------- #
FROM nginx:1.27-alpine AS runtime

# Full server configuration for the SPA and the backend reverse proxy.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Only the build output is copied from the builder stage.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# Keep Nginx in the foreground so the container stays alive and receives
# signals; the official image's entrypoint would do the same, but being
# explicit keeps this independent of base-image defaults.
CMD ["nginx", "-g", "daemon off;"]
