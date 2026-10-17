# Talkse voice receptionist (FastAPI). Built from the repo root so the existing
# Alternate Clouds service keeps working; the code lives in talkse/.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY talkse/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY talkse/ .

# Must match the PORT set in the Alternate Clouds Source tab (else 502 Bad Gateway)
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
