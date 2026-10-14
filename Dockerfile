FROM node:22-alpine
WORKDIR /app
COPY or-proxy.mjs .
ENV PORT=8787
EXPOSE 8787
CMD ["node", "or-proxy.mjs"]
