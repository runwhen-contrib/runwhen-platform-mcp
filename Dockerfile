FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8000

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    FASTMCP_STATELESS_HTTP=true

ENTRYPOINT ["runwhen-platform-mcp"]
