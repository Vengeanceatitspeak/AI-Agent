"""FastAPI application — local REST + WebSocket API for JARVIS.

Endpoints:
    POST /api/chat          — Send a message, get a response
    WS   /ws/chat           — WebSocket for streaming chat
    GET  /api/health        — Health check
    GET  /api/servers        — Server status
    GET  /api/audit/tail    — Recent audit entries
    POST /api/panic         — Activate panic mode
    DELETE /api/panic       — Deactivate panic mode
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from jarvis.config import AppConfig

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Chat request body."""

    message: str
    session_id: str = ""
    trace_id: str = ""


class ChatResponse(BaseModel):
    """Chat response body."""

    content: str
    trace_id: str
    session_id: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    iterations: int = 0
    usage: dict[str, int] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = "0.1.0"
    model: str = ""
    servers: dict[str, Any] = Field(default_factory=dict)


class PanicRequest(BaseModel):
    """Panic mode request."""

    reason: str = "Manual activation"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(config: AppConfig) -> FastAPI:
    """Create the FastAPI application.

    Args:
        config: Application configuration.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="JARVIS API",
        description="Local AI assistant API",
        version="0.1.0",
    )

    # CORS — restricted to localhost
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store config in app state
    app.state.config = config
    app.state.panic_mode = False

    # Lazy-initialized components (set up in lifespan)
    app.state.agent = None
    app.state.session_mgr = None
    app.state.llm_factory = None

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    @app.on_event("startup")
    async def startup() -> None:
        """Initialize components on startup."""
        from jarvis.core.agent import Agent
        from jarvis.core.session import SessionManager
        from jarvis.llm.factory import LLMFactory

        try:
            factory = LLMFactory(config)
            llm = factory.get_reasoning_model()
            session_mgr = SessionManager(config)
            system_prompt = session_mgr.build_system_prompt()

            agent = Agent(
                llm_provider=llm,
                config=config,
                system_prompt=system_prompt,
            )

            app.state.llm_factory = factory
            app.state.session_mgr = session_mgr
            app.state.agent = agent

            logger.info("api_started", host=config.jarvis.api.host, port=config.jarvis.api.port)
        except Exception as e:
            logger.error("api_startup_failed", error=str(e))

    # ---------------------------------------------------------------------------
    # Routes
    # ---------------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Health check endpoint."""
        return HealthResponse(
            status="ok",
            model=config.jarvis.models.reasoning_model.model,
        )

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        """Send a message and get a response."""
        from jarvis.core.agent import AgentRequest

        if app.state.agent is None:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        session_mgr = app.state.session_mgr
        session = session_mgr.get_or_create_session(request.session_id)
        system_prompt = session_mgr.build_system_prompt()
        messages = session_mgr.assemble_messages(
            session, request.message, system_prompt
        )

        agent_request = AgentRequest(
            message=request.message,
            session_id=session.session_id,
            trace_id=request.trace_id or "",
            origin="api",
        )

        response = await app.state.agent.run(agent_request, messages=messages)

        session.add_assistant_message(response.content)

        return ChatResponse(
            content=response.content,
            trace_id=response.trace_id,
            session_id=session.session_id,
            tool_calls=response.tool_calls_made,
            iterations=response.iterations,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            },
        )

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        """WebSocket endpoint for streaming chat."""
        from jarvis.core.agent import AgentRequest

        await websocket.accept()

        if app.state.agent is None:
            await websocket.send_json({"error": "Agent not initialized"})
            await websocket.close()
            return

        session_mgr = app.state.session_mgr
        session = session_mgr.create_session()

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    msg = {"message": data}

                user_message = msg.get("message", "")
                if not user_message:
                    continue

                system_prompt = session_mgr.build_system_prompt()
                messages = session_mgr.assemble_messages(
                    session, user_message, system_prompt
                )

                agent_request = AgentRequest(
                    message=user_message,
                    session_id=session.session_id,
                    origin="websocket",
                )

                async def on_chunk(text: str) -> None:
                    await websocket.send_json({
                        "type": "text",
                        "content": text,
                    })

                async def on_tool_call(name: str, args: dict) -> None:
                    await websocket.send_json({
                        "type": "tool_call",
                        "tool": name,
                        "arguments": args,
                    })

                async def on_tool_result(name: str, result: str, is_error: bool) -> None:
                    await websocket.send_json({
                        "type": "tool_result",
                        "tool": name,
                        "success": not is_error,
                    })

                response = await app.state.agent.run(
                    agent_request,
                    messages=messages,
                    on_text_chunk=on_chunk,
                    on_tool_call=on_tool_call,
                    on_tool_result=on_tool_result,
                )

                session.add_assistant_message(response.content)

                await websocket.send_json({
                    "type": "done",
                    "trace_id": response.trace_id,
                    "iterations": response.iterations,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                })

        except WebSocketDisconnect:
            logger.info("websocket_disconnected", session_id=session.session_id)

    @app.post("/api/panic")
    async def activate_panic(request: PanicRequest) -> dict:
        """Activate panic mode."""
        app.state.panic_mode = True
        logger.critical("panic_activated_via_api", reason=request.reason)
        return {"status": "panic_activated", "reason": request.reason}

    @app.delete("/api/panic")
    async def deactivate_panic() -> dict:
        """Deactivate panic mode."""
        app.state.panic_mode = False
        logger.info("panic_deactivated_via_api")
        return {"status": "panic_deactivated"}

    @app.get("/api/audit/tail")
    async def audit_tail(n: int = 20) -> list[dict]:
        """Get recent audit entries."""
        from jarvis.policy.audit import AuditLog
        data_dir = Path.home() / ".jarvis" / "data"
        audit = AuditLog(data_dir / "audit.db")
        await audit.initialize()
        return await audit.tail(limit=n)

    # Serve the minimal web client
    import os
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def root():
        """Serve the web client."""
        index_path = Path(__file__).parent / "static" / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {"message": "JARVIS API is running. Web client not found."}

    return app
