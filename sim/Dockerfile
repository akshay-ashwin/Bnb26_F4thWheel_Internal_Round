FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:${PATH}"

RUN pip install --no-cache-dir uv==0.12.19

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
# The container installs the optional uvloop extra; native Windows/macOS runs do not need it.
RUN uv sync --frozen --extra fast

COPY . .
CMD ["sim"]
