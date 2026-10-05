FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY contratacao contratacao
COPY sql sql
COPY static static
ENV DIR_DADOS=/tmp/dados PYTHONUNBUFFERED=1
CMD ["uvicorn", "contratacao.api:app", "--host", "0.0.0.0", "--port", "8000"]
