FROM python:3.11-slim
WORKDIR /app
COPY tools/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY tools/virtual_tasmota.py /app/virtual_tasmota.py
CMD ["python", "-u", "/app/virtual_tasmota.py"]
