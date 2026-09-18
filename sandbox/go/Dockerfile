FROM golang:1.22-bookworm

WORKDIR /workspace

# No application code is copied in -- file_changes/test_files are bind-mounted
# into /workspace at runtime by tools/sandbox_tools.py. `go test`/`go vet`
# download the target project's own module dependencies (per its go.mod) at
# run time.
