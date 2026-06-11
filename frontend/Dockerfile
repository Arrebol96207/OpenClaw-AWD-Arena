# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies from the lockfile for reproducible image builds.
RUN npm config set fetch-retries 5 && \
    npm config set fetch-retry-mintimeout 20000 && \
    npm config set fetch-retry-maxtimeout 120000 && \
    npm ci --no-audit --no-fund

# Copy source code
COPY . .

# Set API base to empty so it uses relative paths for Nginx proxy
ENV VITE_API_BASE=""

# Build the app
RUN npm run build

# Serve stage
FROM nginx:1.27-alpine

RUN apk add --no-cache tzdata

ENV TZ=Asia/Shanghai

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf

# Expose port 80
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
