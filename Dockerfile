FROM python:3.12-slim

ARG GIT_SHA=""
ARG GIT_REF=""

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8000

ENV MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    FASTMCP_STATELESS_HTTP=true \
    GIT_SHA=${GIT_SHA} \
    GIT_REF=${GIT_REF}

ENTRYPOINT ["runwhen-platform-mcp"]
