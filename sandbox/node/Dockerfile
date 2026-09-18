FROM node:20-slim

WORKDIR /workspace

# No application code is copied in -- file_changes/test_files are bind-mounted
# into /workspace at runtime by tools/sandbox_tools.py. Dependencies (npm
# ci/install) and any project-local eslint are installed at container-run
# time, from the target project's own package.json, since they vary per repo.
