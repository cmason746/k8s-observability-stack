# Same pattern as the orders and inventory images, but no web server:
# it just runs the script, which loops forever sending requests
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --create-home appuser
USER appuser

CMD ["python", "app.py"]
