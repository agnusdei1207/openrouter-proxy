FROM python:3-alpine
WORKDIR /app
COPY or-proxy.py .
ENV PORT=8787
EXPOSE 8787
CMD ["python", "or-proxy.py"]
