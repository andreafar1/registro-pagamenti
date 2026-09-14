FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates templates
COPY static static
RUN mkdir -p /app/data && useradd -r -u 10001 appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8080
CMD ["gunicorn","--bind","0.0.0.0:8080","--workers","2","--threads","4","--access-logfile","-","app:app"]
