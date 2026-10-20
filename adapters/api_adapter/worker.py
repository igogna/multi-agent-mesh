import json
import asyncio
import sys
import time
import redis.asyncio as aioredis
from redis.exceptions import TimeoutError as RedisTimeoutError
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg_pool import AsyncConnectionPool
from psycopg import AsyncConnection
from core._llm import reset_tokens, consume_step_tokens, get_accumulated_tokens, set_job_api_key

async def update_execution_db(execution_id: int, status: str, branch_name: str = None, pr_url: str = None, commit_sha: str = None, token_usage: int = None):
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

import os
import time
from tools import github_tools
from adapters.cli_adapter.run import make_branch_name, build_commit_message, build_pr_title, build_pr_body_with_ticket
from adapters.langgraph_adapter.graph import build_graph
from core.models import RunState

# Replace with your actual database credentials
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/langgraph_db"
REDIS_URL = "redis://localhost:6379"

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

    # 4. Setup LangGraph Config
    config = {"configurable": {"thread_id": ticket_id}}
    initial_state = {
        "requirement": requirement,
        "repo_url": "D:\\ai-knowledge-assistant", # Point to the target project!
        "base_branch": job_data.get("base_branch", "main"),
        "target_repo": job_data.get("target_repo"),
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
        await update_execution_db(execution_id, "FAILED")
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
                github_token = job_data.get("github_token")
                if github_token:
                    os.environ["GITHUB_TOKEN"] = github_token
                    target_repo = run_state.get("target_repo") or job_data.get("target_repo") or "ksingh123456789/ai-knowledge-assistant"
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
                    print(f"[{ticket_id}] Cannot create PR: No github token provided!")

    except Exception as e:
        print(f"[{ticket_id}] Error during resume execution: {e}")
        await redis_client.publish("ui_updates", json.dumps({
            "ticket_id": ticket_id,
            "event_type": "error",
            "node": "worker",
            "payload": str(e)
        }))

async def main():
    print("Initializing Python Worker...")
    
    # 1. Connect to Redis (with health checks so it doesn't timeout when idle)
    redis_client = aioredis.Redis.from_url(REDIS_URL, health_check_interval=30)
    print("Connected to Redis.")

    # 2. Setup the LangGraph Checkpointer (Postgres)
    print(f"Connecting to database: {DATABASE_URL}...")
    
    # from_conn_string handles the connection pool and transaction logic for us
    async with AsyncPostgresSaver.from_conn_string(DATABASE_URL) as checkpointer:
        # This automatically creates the required tables in Postgres if they don't exist
        await checkpointer.setup()
        
        # Link the DB to your graph!
        graph = build_graph(checkpointer=checkpointer)
        print("Graph compiled successfully. Worker is listening for jobs...")

        # 3. Infinite loop: Watch the Redis Queue
        while True:
            # BRPOP blocks and waits until a job is pushed to 'agent_jobs'
            # We use timeout=5 instead of 0 to prevent the network connection from dying while idle
            try:
                result = await redis_client.brpop(['agent_resume_jobs', 'agent_jobs'], timeout=5)
            except (TimeoutError, RedisTimeoutError, asyncio.CancelledError):
                continue
            
            if not result:
                continue
                
            queue_name, raw_data = result
            
            if queue_name.decode('utf-8') == 'agent_jobs':
                asyncio.create_task(process_job(raw_data, graph, redis_client))
            elif queue_name.decode('utf-8') == 'agent_resume_jobs':
                asyncio.create_task(resume_job(raw_data, graph, redis_client))
if __name__ == "__main__":
    if sys.platform == 'win32':
        import selectors
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
