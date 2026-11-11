import asyncio
import os
import sys
from adapters.api_adapter.worker import main as run_worker

# Ensure git identity fallbacks are present in container environments
os.environ.setdefault("GIT_AUTHOR_NAME", "AgentDev Bot")
os.environ.setdefault("GIT_AUTHOR_EMAIL", "agentdev-bot@users.noreply.github.com")
os.environ.setdefault("GIT_COMMITTER_NAME", "AgentDev Bot")
os.environ.setdefault("GIT_COMMITTER_EMAIL", "agentdev-bot@users.noreply.github.com")

async def handle_http_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Simple lightweight HTTP handler for Render health checks (no external dependencies needed)."""
    try:
        # Read the incoming HTTP request line (e.g. GET / HTTP/1.1)
        line = await reader.readline()
        # Drain the rest of the HTTP headers
        while line and line != b"\r\n" and line != b"\n":
            line = await reader.readline()

        body = b'{"status":"healthy","service":"multi-agent-mesh"}\n'
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        writer.write(response)
        await writer.drain()
    except Exception as e:
        print(f"Error handling HTTP request: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def start_services():
    port = int(os.environ.get("PORT", 10000))
    host = "0.0.0.0"

    print(f"Starting HTTP health server on http://{host}:{port}...")
    server = await asyncio.start_server(handle_http_request, host, port)
    print(f"HTTP health server listening on port {port}.")

    print("Launching background AI worker...")
    worker_task = asyncio.create_task(run_worker())

    # Keep both the health server and the worker running
    await asyncio.gather(
        server.serve_forever(),
        worker_task
    )

if __name__ == "__main__":
    if sys.platform == 'win32':
        import selectors
        asyncio.run(start_services(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(start_services())

