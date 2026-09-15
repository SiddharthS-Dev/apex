# ---------------------------------------------------------------
# APEX backend
# ---------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/apex

# Dependencies are installed from pyproject alone so that this layer is
# cached until the dependency set itself changes.
COPY pyproject.toml ./
COPY app/__init__.py ./app/__init__.py
RUN pip install --upgrade pip && pip install -e ".[dev]"

COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 apex && chown -R apex:apex /srv/apex
USER apex

EXPOSE 8000

# ---------------------------------------------------------------
FROM base AS development
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------
FROM base AS production
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
