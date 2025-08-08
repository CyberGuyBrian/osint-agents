FROM mcr.microsoft.com/devcontainers/base:ubuntu
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git curl jq dnsutils tesseract-ocr libgbm1 libnss3 ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*