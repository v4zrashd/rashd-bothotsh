FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    libzbar0 \
    zbar-tools \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose port (Render/Railway use dynamic port via environment variable, but 8080 is standard default)
EXPOSE 8080

# Make start script executable
RUN chmod +x start.sh

# Run start script
CMD ["./start.sh"]
