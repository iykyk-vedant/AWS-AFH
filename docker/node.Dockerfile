FROM node:20-slim

WORKDIR /app

# Pre-install common test dependencies
RUN npm install -g jest@29

# Use the existing 'node' user (UID 1000) for non-root execution
USER node
CMD ["node", "--version"]
