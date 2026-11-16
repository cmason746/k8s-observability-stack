# Start from an official image that already has Python installed
FROM python:3.12-slim

# All following commands run inside /app in the image
WORKDIR /app

# Install dependencies first. Docker caches each step, so this slow step is
# only redone when requirements.txt changes, not on every code change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then copy in the code
COPY app.py .

# Run as a normal user rather than root, which is safer
RUN useradd --create-home appuser
USER appuser

# Document the port the app listens on
EXPOSE 8000

# The command that runs when a container starts from this image
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
