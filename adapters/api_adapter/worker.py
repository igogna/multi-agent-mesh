import json
import asyncio
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()
import redis.asyncio as aioredis
from redis.exceptions import TimeoutError as RedisTimeoutError, ConnectionError as RedisConnectionError
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg_pool import AsyncConnectionPool
from psycopg import AsyncConnection
from core._llm import reset_tokens, consume_step_tokens, get_accumulated_tokens, set_job_api_key

# Configuration loaded from environment variables (.env)
def clean_redis_url(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    url = raw_url.strip().strip('"').strip("'")
    if " -u " in url:
        url = url.split(" -u ")[-1].strip()
    elif url.startswith("redis-cli"):
        for part in url.split():
            if part.startswith("redis://") or part.startswith("rediss://"):
                url = part
                break
    if "upstash.io" in url and url.startswith("redis://"):
        url = url.replace("redis://", "rediss://", 1)
    return url

DATABASE_URL = os.getenv("DATABASE_URL", "")
REDIS_URL = clean_redis_url(os.getenv("REDIS_URL"))

async def update_execution_db(execution_id: int, status: str, branch_name: str = None, pr_url: str = None, commit_sha: str = None, token_usage: int = None, error: str = None):
    if not execution_id:
        return
    query = "UPDATE executions SET status = %s"
    params = [status]
    if branch_name:
        query += ", branch_name = %s"
        params.append(branch_name)
    if pr_url:
        query += ", pull_request_url = %s"
        params.append(pr_url)
    if commit_sha:
        query += ", commit_sha = %s"
        params.append(commit_sha)
    if error:
        query += ", error = %s"
        params.append(error)
    
    if status in ["COMPLETED", "FAILED"]:
        query += ", completed_at = CURRENT_TIMESTAMP"
        
    if token_usage is not None:
        query += ", token_usage = %s"
        params.append(token_usage)
        
    query += " WHERE id = %s"
    params.append(execution_id)
    
    try:
        async with await AsyncConnection.connect(DATABASE_URL) as conn:
            await conn.execute(query, tuple(params))
            await conn.commit()
    except Exception as e:
        print(f"Failed to update execution {execution_id} to {status}: {e}")

async def save_execution_step(execution_id: int, name: str, latency: str = "0.0s", tokens: str = "0 tokens", detail: str = None):
    if not execution_id: return
    query = "INSERT INTO execution_steps (execution_id, name, latency, tokens, detail) VALUES (%s, %s, %s, %s, %s)"
    try:
        async with await AsyncConnection.connect(DATABASE_URL) as conn:
            await conn.execute(query, (execution_id, name, latency, tokens, detail))
            await conn.commit()
    except Exception as e:
        print(f"Failed to save step: {e}")

async def save_execution_files(execution_id: int, file_changes: list):
    if not execution_id or not file_changes: return
    query = "INSERT INTO execution_files (execution_id, name, added_lines, deleted_lines, description) VALUES (%s, %s, %s, %s, %s)"
    try:
        async with await AsyncConnection.connect(DATABASE_URL) as conn:
            for change in file_changes:
                # Fallback dictionary access since it might be serialized to dict
                diff = change.get("diff", "") if isinstance(change, dict) else change.diff
                path = change.get("path", "unknown") if isinstance(change, dict) else change.path
                added = sum(1 for line in diff.split("\n") if line.startswith("+") and not line.startswith("+++"))
                deleted = sum(1 for line in diff.split("\n") if line.startswith("-") and not line.startswith("---"))
                await conn.execute(query, (execution_id, path, added, deleted, "Modified file"))
            await conn.commit()
    except Exception as e:
        print(f"Failed to save files: {e}")

from tools import github_tools
from adapters.cli_adapter.run import make_branch_name, build_commit_message, build_pr_title, build_pr_body_with_ticket
from adapters.langgraph_adapter.graph import build_graph
from core.models import RunState

async def process_job(raw_data, graph, redis_client):
    """Handles the execution of a single ticket so multiple can run concurrently."""
    job_data = json.loads(raw_data)
    
    ticket_id = job_data["ticket_id"]
    requirement = job_data["requirement"]
    execution_id = job_data.get("execution_id")
    print(f"[{ticket_id}] Picked up new job from queue! Exec ID: {execution_id}")
    print(f"[{ticket_id}] Requirement: {requirement}")

    anthropic_key = job_data.get("anthropic_api_key")
    if anthropic_key:
        set_job_api_key(anthropic_key)
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key
        print(f"[{ticket_id}] Configured authenticated Anthropic API key from job payload.")

    await update_execution_db(execution_id, "RUNNING")

    # Resolve target repository path and token dynamically
    target_repo = job_data.get("target_repo") or os.getenv("GITHUB_REPO")
    github_token = job_data.get("github_token") or os.getenv("GITHUB_TOKEN")
    repo_path = job_data.get("repo_path") or job_data.get("repo_url")

    if target_repo and github_token:
        try:
            repo_obj = github_tools._local_clone(target_repo, github_token)
            repo_path = str(repo_obj.working_tree_dir)
            print(f"[{ticket_id}] Cloned target repository {target_repo} to {repo_path}")
        except Exception as clone_err:
            print(f"[{ticket_id}] Error cloning remote repo {target_repo}: {clone_err}")
            if not repo_path:
                clone_msg = f"Failed to clone repository {target_repo}: {clone_err}"
                await update_execution_db(execution_id, "FAILED", error=clone_msg)
                await redis_client.publish("ui_updates", json.dumps({
                    "ticket_id": ticket_id,
                    "event_type": "error",
                    "node": "worker",
                    "payload": clone_msg
                }))
                return

    if not repo_path:
        error_msg = f"Cannot process job: target_repo and github_token were not provided, and no local repo_path was found."
        print(f"[{ticket_id}] {error_msg}")
        await update_execution_db(execution_id, "FAILED", error=error_msg)
        await redis_client.publish("ui_updates", json.dumps({
            "ticket_id": ticket_id,
            "event_type": "error",
            "node": "worker",
            "payload": error_msg
        }))
        return

    # 4. Setup LangGraph Config
    config = {"configurable": {"thread_id": ticket_id}}
    initial_state = {
        "requirement": requirement,
        "repo_url": repo_path,
        "base_branch": job_data.get("base_branch", "main"),
        "target_repo": target_repo,
        "ticket_type": job_data.get("ticket_type", "task"),
        "skip_tests": True
    }

    # 5. Execute LangGraph!
    print(f"[{ticket_id}] Starting AI execution...")
    reset_tokens()
    node_start_times = {}
    try:
        async for event in graph.astream_events(initial_state, config, version="v1"):
            event_type = event.get("event")
            node_name = event.get("name")
            
            if event_type == "on_chain_start" and node_name in ['analyze', 'generate', 'generate_tests', 'run_tests', 'review']:
                node_start_times[node_name] = time.time()

            # Publish progress updates back to Redis (so React can see them later)
            event_message = json.dumps({
                "ticket_id": ticket_id, 
                "event_type": event["event"],
                "node": event.get("metadata", {}).get("langgraph_node", node_name)
            })
            await redis_client.publish("ui_updates", event_message)
            
            if event["event"] == "on_chat_model_stream":
                 pass # Too noisy to print every token
            else:
                 print(f"[{ticket_id}] {event['event']} -> {node_name}")
                 if event["event"] == "on_chain_end" and node_name in ['analyze', 'generate', 'generate_tests', 'run_tests', 'review']:
                     start_t = node_start_times.pop(node_name, time.time())
                     latency_sec = max(0.1, time.time() - start_t)
                     latency_str = f"{latency_sec:.2f}s"
                     step_tokens = consume_step_tokens()
                     tokens_str = f"{step_tokens} tokens"
                     await save_execution_step(execution_id, node_name, latency=latency_str, tokens=tokens_str, detail=f"Completed node {node_name}")

        print(f"[{ticket_id}] Execution completed (or paused at a human gate)!")
        
        # Check if graph is paused on an interrupt
        state = await graph.aget_state(config)
        if state.next:
            tasks = state.tasks
            if tasks and tasks[0].interrupts:
                await update_execution_db(execution_id, "NEEDS_REVIEW")
                interrupt_val = tasks[0].interrupts[0].value
                await redis_client.publish("ui_updates", json.dumps({
                    "ticket_id": ticket_id,
                    "event_type": "interrupt",
                    "node": tasks[0].name,
                    "payload": interrupt_val
                }))
        else:
            total_tokens = get_accumulated_tokens()["total_tokens"]
            await update_execution_db(execution_id, "COMPLETED", token_usage=total_tokens)
    except Exception as e:
        print(f"[{ticket_id}] Error during execution: {e}")
        await update_execution_db(execution_id, "FAILED", error=str(e))
        await redis_client.publish("ui_updates", json.dumps({
            "ticket_id": ticket_id,
            "event_type": "error",
            "node": "worker",
            "payload": str(e)
        }))

async def resume_job(raw_data, graph, redis_client):
    job_data = json.loads(raw_data)
    ticket_id = job_data["ticket_id"]
    decision = job_data["decision"]
    feedback = job_data.get("feedback")
    
    anthropic_key = job_data.get("anthropic_api_key")
    if anthropic_key:
        set_job_api_key(anthropic_key)
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key
        print(f"[{ticket_id}] Configured authenticated Anthropic API key from resume job payload.")

    config = {"configurable": {"thread_id": ticket_id}}
    resume_payload = {"decision": decision}
    if feedback:
         resume_payload["feedback"] = feedback
         
    print(f"[{ticket_id}] Resuming execution with decision: {decision}")
    node_start_times = {}
    try:
        async for event in graph.astream_events(Command(resume=resume_payload), config, version="v1"):
            event_type = event.get("event")
            node_name = event.get("name")
            
            if event_type == "on_chain_start" and node_name in ['analyze', 'generate', 'generate_tests', 'run_tests', 'review']:
                node_start_times[node_name] = time.time()

            event_message = json.dumps({
                "ticket_id": ticket_id, 
                "event_type": event["event"],
                "node": event.get("metadata", {}).get("langgraph_node", node_name)
            })
            await redis_client.publish("ui_updates", event_message)
            
            if event["event"] != "on_chat_model_stream":
                 print(f"[{ticket_id}] [RESUME] {event['event']} -> {node_name}")
                 if event["event"] == "on_chain_end" and node_name in ['analyze', 'generate', 'generate_tests', 'run_tests', 'review']:
                     execution_id = job_data.get("execution_id")
                     start_t = node_start_times.pop(node_name, time.time())
                     latency_sec = max(0.1, time.time() - start_t)
                     latency_str = f"{latency_sec:.2f}s"
                     step_tokens = consume_step_tokens()
                     tokens_str = f"{step_tokens} tokens"
                     await save_execution_step(execution_id, node_name, latency=latency_str, tokens=tokens_str, detail=f"Completed node {node_name} (Resume)")
                 
        print(f"[{ticket_id}] Resume execution completed!")
        
        # Check if the graph finished (no next node)
        state = await graph.aget_state(config)
        if not state.next:
            print(f"[{ticket_id}] Graph completed! Pushing code and creating PR...")
            run_state = state.values
            
            if run_state.get("file_changes"):
                # Authenticate github_tools
                github_token = job_data.get("github_token") or os.getenv("GITHUB_TOKEN")
                target_repo = run_state.get("target_repo") or job_data.get("target_repo") or os.getenv("GITHUB_REPO")
                if github_token and target_repo:
                    os.environ["GITHUB_TOKEN"] = github_token
                    os.environ["GITHUB_REPO"] = target_repo
                    
                    login = github_tools.get_current_login()
                    ticket_type = run_state.get("ticket_type") or job_data.get("ticket_type") or "task"
                    branch_name = make_branch_name(ticket_id, run_state["requirement"], login, ticket_type)
                    
                    print(f"[{ticket_id}] Creating branch {branch_name}...")
                    github_tools.create_branch(target_repo, run_state["base_branch"], branch_name)
                    
                    print(f"[{ticket_id}] Committing and pushing...")
                    github_tools.commit_and_push(
                        target_repo,
                        branch_name,
                        run_state["file_changes"],
                        run_state.get("test_files", []),
                        commit_message=build_commit_message(ticket_id, run_state["plan"])
                    )
                    
                    print(f"[{ticket_id}] Opening PR...")
                    pr_num, pr_url = github_tools.open_pr(
                        target_repo,
                        branch_name,
                        run_state["base_branch"],
                        title=build_pr_title(ticket_id, run_state["plan"]),
                        body=build_pr_body_with_ticket(RunState(**run_state))
                    )
                    print(f"[{ticket_id}] PR Created successfully: {pr_url}")
                    
                    execution_id = job_data.get("execution_id")
                    
                    # Save generated files to Trace DB!
                    await save_execution_files(execution_id, run_state["file_changes"])
                    
                    # Update branch, PR URL and real dynamic token usage
                    total_tokens = get_accumulated_tokens()["total_tokens"]
                    await update_execution_db(execution_id, "COMPLETED", branch_name=branch_name, pr_url=pr_url, token_usage=total_tokens)
                    
                    # Notify UI that we are done!
                    await redis_client.publish("ui_updates", json.dumps({
                        "ticket_id": ticket_id,
                        "event_type": "pr_created",
                        "pr_url": pr_url
                    }))
                else:
                    print(f"[{ticket_id}] Cannot create PR: Missing github_token or target_repo!")

    except Exception as e:
        print(f"[{ticket_id}] Error during resume execution: {e}")
        execution_id = job_data.get("execution_id")
        await update_execution_db(execution_id, "FAILED", error=str(e))
        await redis_client.publish("ui_updates", json.dumps({
            "ticket_id": ticket_id,
            "event_type": "error",
            "node": "worker",
            "payload": str(e)
        }))

async def main():
    print("Initializing Python Worker...")
    
    database_url = os.getenv("DATABASE_URL") or DATABASE_URL
    redis_url = clean_redis_url(os.getenv("REDIS_URL") or REDIS_URL)

    if not redis_url:
        print("ERROR: REDIS_URL is not configured in .env or environment variables.")
        return
    if not database_url:
        print("ERROR: DATABASE_URL is not configured in .env or environment variables.")
        return

    # 1. Connect to Redis and verify connection (with retry loop)
    print(f"Connecting to Redis: {redis_url}...")
    redis_client = aioredis.Redis.from_url(redis_url, health_check_interval=30)
    while True:
        try:
            await redis_client.ping()
            print("Connected to Redis successfully.")
            break
        except (RedisConnectionError, OSError) as e:
            print(f"Waiting for Redis at {redis_url}: {e}. Retrying in 10s...")
            await asyncio.sleep(10)

    # 2. Setup the LangGraph Checkpointer (Postgres with retry loop)
    print(f"Connecting to database: {database_url}...")
    while True:
        try:
            async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
                await checkpointer.setup()
                graph = build_graph(checkpointer=checkpointer)
                print("Graph compiled successfully. Worker is listening for jobs...")

                # 3. Infinite loop: Watch the Redis Queue
                while True:
                    try:
                        result = await redis_client.brpop(['agent_resume_jobs', 'agent_jobs'], timeout=5)
                    except (TimeoutError, RedisTimeoutError, asyncio.CancelledError):
                        continue
                    except (RedisConnectionError, OSError) as e:
                        print(f"Redis connection error: {e}. Retrying in 5 seconds...")
                        await asyncio.sleep(5)
                        continue
                    
                    if not result:
                        continue
                        
                    queue_name, raw_data = result
                    
                    if queue_name.decode('utf-8') == 'agent_jobs':
                        asyncio.create_task(process_job(raw_data, graph, redis_client))
                    elif queue_name.decode('utf-8') == 'agent_resume_jobs':
                        asyncio.create_task(resume_job(raw_data, graph, redis_client))
        except Exception as db_err:
            print(f"Database connection error: {db_err}. Retrying in 10s...")
            await asyncio.sleep(10)
if __name__ == "__main__":
    if sys.platform == 'win32':
        import selectors
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
