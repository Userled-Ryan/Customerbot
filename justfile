default:
    @just --list

# Install dependencies and pre-commit hooks
install:
    uv sync --dev
    uv run pre-commit install

# Start FastAPI development server
dev:
    uv run uvicorn prbot.main:api --reload

# Run pytest tests
test *args:
    uv run pytest {{ args }}

# Lint code with ruff
lint:
    uv run ruff check .

# Lint and auto-fix
lint-fix:
    uv run ruff check --fix .

# Format code with ruff
format:
    uv run ruff format .

# Check code formatting
format-check:
    uv run ruff format --check .

# Type check with ty
typecheck:
    uv run ty check

# Check Alembic migrations are up to date with models
check-migrations:
    uv run python scripts/check_migrations.py

# Run all checks (lint + format-check + typecheck + migrations)
check: lint format-check typecheck check-migrations

# Remove Python cache files
clean:
    find . -type f -name "*.pyc" -delete
    find . -type d -name "__pycache__" -delete
