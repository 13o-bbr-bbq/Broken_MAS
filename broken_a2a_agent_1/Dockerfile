FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt uvicorn[standard]

COPY . .

EXPOSE 9011
CMD ["python", "broken_a2a_agent_1.py"]
