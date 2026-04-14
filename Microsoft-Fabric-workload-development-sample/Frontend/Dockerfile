FROM node:20-alpine AS base
WORKDIR /app

# Install dependencies first for better caching
COPY Frontend/package.json Frontend/package-lock.json* ./
# Raise heap limit — @fluentui tree exceeds Node's default 2GB during npm ci
RUN NODE_OPTIONS="--max-old-space-size=4096" npm ci

# Copy frontend source
COPY Frontend/ ./
# Copy root .env for env-cmd
COPY .env ../.env

EXPOSE 60006

# webpack-dev-server with hot reload
CMD ["npx", "env-cmd", "-f", "../.env", "webpack", "serve", "--config", "./tools/webpack.config.js", "--host", "0.0.0.0"]
