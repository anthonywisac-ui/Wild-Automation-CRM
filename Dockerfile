FROM python:3.10-slim

# Install Node.js 20 + Chromium for whatsapp-web.js
RUN apt-get update && apt-get install -y \
    curl gnupg ca-certificates \
    chromium \
    --no-install-recommends && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node deps for wa-bridge
COPY wa-bridge/package.json wa-bridge/
RUN cd wa-bridge && npm install --omit=dev

# Copy all app code
COPY . .

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
