# OPEN-ATLAS quant engine API — non-root runtime.
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" numpy scipy \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd -r -s /usr/sbin/nologin quant
USER quant
EXPOSE 9102
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "9102"]
