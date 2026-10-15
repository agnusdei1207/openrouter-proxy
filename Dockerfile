FROM python:3-alpine
WORKDIR /app
COPY or-proxy.py config.json ./
EXPOSE 8787
CMD ["python", "or-proxy.py"]
