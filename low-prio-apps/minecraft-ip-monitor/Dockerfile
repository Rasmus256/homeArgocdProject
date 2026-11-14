FROM python:3.15-alpine

RUN apk add --no-cache ca-certificates
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY app.py /app/app.py
USER 10001:10001
EXPOSE 8080
CMD ["python", "/app/app.py"]
