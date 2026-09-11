FROM python:3.11-slim

# Create non-root user for sandbox execution
RUN useradd -m -u 1000 sandbox

WORKDIR /app

# Pre-install common test dependencies
RUN pip install --no-cache-dir pytest pytest-json-report

USER sandbox
CMD ["python", "--version"]
