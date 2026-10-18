FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock
RUN useradd --uid 10001 --create-home appuser && mkdir -p /data && chown appuser:appuser /data
COPY --chown=appuser:appuser . .
USER appuser
ENV STOCKOUT_DB=/data/stockout.db
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
