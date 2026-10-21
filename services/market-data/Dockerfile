FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY market_data ./market_data
RUN pip install --no-cache-dir .
ENV MD_WS_HOST=0.0.0.0 MD_WS_PORT=9103
EXPOSE 9103
CMD ["python", "-m", "market_data"]
