FROM python:3.11-slim
WORKDIR /app
COPY tools/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY tools/virtual_tasmota.py tools/tasmota_bridge.py /app/tools/
CMD ["python", "-u", "-m", "tools.tasmota_bridge"]
