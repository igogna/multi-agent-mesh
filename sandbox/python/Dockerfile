FROM python:3.11-slim

WORKDIR /workspace

RUN pip install --no-cache-dir pytest pytest-cov ruff

# No application code is copied in -- file_changes/test_files are bind-mounted
# into /workspace at runtime by tools/sandbox_tools.py.
