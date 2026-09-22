FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Set environment
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Bangkok
ENV PORT=7860

# Set up non-root user with UID 1000 for Hugging Face Spaces
RUN useradd -m -u 1000 user

WORKDIR /app

# Install system utilities & fonts for Thai language
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    fonts-thai-tlwg \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and set permissions
COPY . .
RUN mkdir -p /app/screenshots /app/static \
    && chown -R user:user /app \
    && chmod -R 777 /app

# Switch to non-root user
USER user

EXPOSE 7860

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
