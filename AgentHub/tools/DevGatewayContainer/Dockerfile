# Use the official .NET 8.0 runtime as the base image.
# Pin to linux/amd64: the DevGateway binary distributed by Microsoft is x64-only,
# and docker-compose.yaml declares `platform: linux/amd64` for this service.
FROM --platform=linux/amd64 mcr.microsoft.com/dotnet/aspnet:8.0 AS base
WORKDIR /app

# DevGateway download link in Microsoft Download Center
ARG DEV_GATEWAY_DOWNLOAD_URL=https://download.microsoft.com/download/c/4/a/c4a0a569-87cd-4633-a81e-26ef3d4266df/DevGateway.zip

# Combine RUN commands to reduce layers
RUN apt-get update && apt-get install -y curl sudo unzip && \
    curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash && \
    curl ${DEV_GATEWAY_DOWNLOAD_URL} -O && \
    unzip -o DevGateway.zip -d DevGateway && \
    rm -rf DevGateway.zip && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /var/cache/apt/*

# Create a non-root user (compose overrides with user: "0" anyway,
# but keep the account for cases where root isn't forced).
RUN useradd -m clippy

USER clippy

# No ENTRYPOINT / CMD here on purpose: docker-compose.yaml defines the
# entrypoint inline (token acquisition + dotnet DevGateway invocation).
