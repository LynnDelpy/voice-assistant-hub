FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1
RUN uv pip install --system --python /usr/local/bin/python3.12 "fastapi>=0.110" "uvicorn[standard]>=0.29"
COPY app.py /app/app.py
WORKDIR /app
EXPOSE 8123
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8123"]
