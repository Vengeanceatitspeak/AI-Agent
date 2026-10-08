"""Jimmy — Personal AI Assistant Server

Self-contained FastAPI server featuring:
- Groq LLM with round-robin key rotation
- ElevenLabs TTS with sentence-level streaming (real-time voice)
- Browser Web Speech API for STT (no server-side STT needed)
- WebSocket streaming for real-time chat
- Persistent memory via GitHub storage
- Reminder / timer system
- Serves a premium web UI
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid
import time
import itertools
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta

import httpx
import structlog
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Load .env
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Groq round-robin client
# ---------------------------------------------------------------------------

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = {
    "fast": "qwen/qwen3.8-27b",
    "default": "openai/gpt-oss-120b",
    "reasoning": "openai/gpt-oss-120b",
}


class GroqRotatingClient:
    """Groq API client with automatic key rotation."""

    def __init__(self):
        raw_keys = os.environ.get("GROQ_API_KEYS", "")
        self.keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if not self.keys:
            raise ValueError("GROQ_API_KEYS not set in environment")
        self._cycle = itertools.cycle(self.keys)
        self._call_count = 0
        logger.info("groq_client_init", num_keys=len(self.keys))

    def _next_key(self) -> str:
        self._call_count += 1
        key = next(self._cycle)
        logger.debug("groq_key_rotated", call_num=self._call_count, key_suffix=key[-6:])
        return key

    async def chat_stream(
        self,
        messages: list[dict],
        model: str = "openai/gpt-oss-120b",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ):
        """Stream chat completions, yielding text chunks or tool calls."""
        key = self._next_key()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", GROQ_API_URL, json=payload, headers=headers
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            if delta:
                                yield delta
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue


# ---------------------------------------------------------------------------
# Edge TTS — completely free neural TTS (Microsoft Azure)
# ---------------------------------------------------------------------------
import edge_tts

class EdgeTTS:
    """Completely free TTS using Microsoft Edge's Azure Neural voices."""
    
    def __init__(self):
        # 'en-US-AvaMultilingualNeural' can speak English and seamlessly switch to Hindi!
        self.voice = "en-US-AvaMultilingualNeural"
        self.api_key = "dummy"  # Keep api_key boolean logic working seamlessly

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to MP3 audio chunks."""
        try:
            # Speed up the voice by 15% for a more natural conversational pace
            communicate = edge_tts.Communicate(text, self.voice, rate="+0%")
            audio_bytes = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
            return bytes(audio_bytes)
        except Exception as e:
            logger.error("edge_tts_error", error=str(e))
            return b""


# ---------------------------------------------------------------------------
# Sentence splitter for real-time TTS
# ---------------------------------------------------------------------------

# Regex to split text at sentence boundaries while keeping the delimiters
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?;])\s+|(?<=\n)')


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentence-ish chunks for TTS.
    Returns a list where each element is a complete sentence.
    """
    parts = _SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# GitHub-backed persistent memory
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"


class GitHubMemory:
    """Persistent memory stored as JSON files in a GitHub repo.

    Structure:
        memory/conversations.json  — recent conversation turns
        memory/user_facts.json     — learned facts about the user
        memory/reminders.json      — scheduled reminders
    """

    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN", "")
        self.repo = os.environ.get("GITHUB_REPO", "")  # e.g. "user/jimmy-memory"
        self.enabled = bool(self.token and self.repo)
        self._cache: dict[str, Any] = {}
        if self.enabled:
            logger.info("github_memory_enabled", repo=self.repo)
        else:
            logger.info("github_memory_disabled", msg="Using local-only memory")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    async def _read_file(self, path: str) -> tuple[Any, str]:
        """Read a JSON file from the repo. Returns (data, sha)."""
        if not self.enabled:
            return self._cache.get(path, {}), ""

        # Check cache first
        if path in self._cache:
            return self._cache[path], self._cache.get(f"{path}_sha", "")

        url = f"{GITHUB_API}/repos/{self.repo}/contents/{path}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 404:
                    return {}, ""
                resp.raise_for_status()
                data = resp.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                parsed = json.loads(content)
                sha = data["sha"]
                self._cache[path] = parsed
                self._cache[f"{path}_sha"] = sha
                return parsed, sha
        except Exception as e:
            logger.error("github_read_error", path=path, error=str(e))
            return self._cache.get(path, {}), ""

    async def _write_file(self, path: str, data: Any, sha: str = ""):
        """Write a JSON file to the repo."""
        self._cache[path] = data

        if not self.enabled:
            return

        url = f"{GITHUB_API}/repos/{self.repo}/contents/{path}"
        content_b64 = base64.b64encode(
            json.dumps(data, indent=2, default=str).encode()
        ).decode()

        payload = {
            "message": f"Jimmy memory update: {path}",
            "content": content_b64,
        }
        if sha:
            payload["sha"] = sha

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.put(url, headers=self._headers(), json=payload)
                if resp.status_code in (200, 201):
                    new_sha = resp.json().get("content", {}).get("sha", "")
                    self._cache[f"{path}_sha"] = new_sha
                    logger.debug("github_write_ok", path=path)
                else:
                    logger.error("github_write_error", path=path, status=resp.status_code,
                                 body=resp.text[:200])
        except Exception as e:
            logger.error("github_write_error", path=path, error=str(e))

    # --- Conversations ---

    async def load_conversations(self) -> list[dict]:
        """Load recent conversation history."""
        data, _ = await self._read_file("memory/conversations.json")
        return data.get("messages", [])

    async def save_conversation_turn(self, role: str, content: str):
        """Append a conversation turn and persist."""
        data, sha = await self._read_file("memory/conversations.json")
        messages = data.get("messages", [])
        messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep last 100 turns to stay within GitHub file size limits
        if len(messages) > 100:
            messages = messages[-100:]
        await self._write_file("memory/conversations.json", {"messages": messages}, sha)

    # --- User facts ---

    async def load_user_facts(self) -> list[str]:
        """Load learned facts about the user."""
        data, _ = await self._read_file("memory/user_facts.json")
        return data.get("facts", [])

    async def save_user_fact(self, fact: str):
        """Add a new fact about the user."""
        data, sha = await self._read_file("memory/user_facts.json")
        facts = data.get("facts", [])
        if fact not in facts:
            facts.append(fact)
        await self._write_file("memory/user_facts.json", {"facts": facts}, sha)

    # --- Reminders ---

    async def load_reminders(self) -> list[dict]:
        """Load all reminders."""
        data, _ = await self._read_file("memory/reminders.json")
        return data.get("reminders", [])

    async def add_reminder(self, text: str, due_at: str) -> dict:
        """Add a new reminder."""
        data, sha = await self._read_file("memory/reminders.json")
        reminders = data.get("reminders", [])
        reminder = {
            "id": f"r_{uuid.uuid4().hex[:8]}",
            "text": text,
            "due_at": due_at,
            "created_at": datetime.now().isoformat(),
            "notified": False,
        }
        reminders.append(reminder)
        await self._write_file("memory/reminders.json", {"reminders": reminders}, sha)
        return reminder

    async def get_due_reminders(self) -> list[dict]:
        """Get reminders that are due now."""
        reminders = await self.load_reminders()
        now = datetime.now()
        due = []
        for r in reminders:
            if r.get("notified"):
                continue
            try:
                due_at = datetime.fromisoformat(r["due_at"])
                if due_at <= now:
                    due.append(r)
            except (ValueError, KeyError):
                continue
        return due

    async def mark_reminder_notified(self, reminder_id: str):
        """Mark a reminder as notified."""
        data, sha = await self._read_file("memory/reminders.json")
        reminders = data.get("reminders", [])
        for r in reminders:
            if r["id"] == reminder_id:
                r["notified"] = True
                break
        await self._write_file("memory/reminders.json", {"reminders": reminders}, sha)


# ---------------------------------------------------------------------------
# Conversation store with memory integration
# ---------------------------------------------------------------------------


class ConversationStore:
    """Conversation manager with persistent memory."""

    def __init__(self, memory: GitHubMemory):
        self._conversations: dict[str, list[dict]] = {}
        self._memory = memory
        self._memory_loaded = False

    async def _build_system_prompt(self) -> str:
        now = datetime.now()

        # Load user facts from memory
        facts = await self._memory.load_user_facts()
        facts_block = ""
        if facts:
            facts_list = "\n".join(f"- {f}" for f in facts[-20:])
            facts_block = f"""
WHAT YOU KNOW ABOUT THE USER:
{facts_list}
"""

        # Load active reminders
        reminders = await self._memory.load_reminders()
        active_reminders = [r for r in reminders if not r.get("notified")]
        reminders_block = ""
        if active_reminders:
            rem_list = "\n".join(
                f"- [{r['id']}] {r['text']} (due: {r['due_at']})"
                for r in active_reminders[-10:]
            )
            reminders_block = f"""
ACTIVE REMINDERS:
{rem_list}
"""

        return f"""You are Jimmy, a warm, intelligent, and proactive personal AI assistant. You speak in a soft, friendly, and conversational tone — like a caring, brilliant friend who happens to know everything. You are NOT a corporate chatbot.

Key traits:
- You are warm, empathetic, and genuinely helpful — you care about the user
- You remember things the user tells you and bring them up when relevant
- You speak naturally and concisely — no robotic formality, no excessive flattery
- When uncertain, you say so honestly rather than guessing
- You proactively offer relevant context, gentle reminders, and helpful suggestions
- You have a light, natural sense of humor — friendly, never forced
- You use the user's name when you know it
- Keep responses short and natural for voice conversation — like how a real person talks

CAPABILITIES:
- You can set reminders and timers. When the user asks you to remind them of something, include this exact tag in your response: [REMINDER: description | YYYY-MM-DDTHH:MM:SS]
  Example: "I'll remind you about that!" [REMINDER: Team meeting | 2026-10-09T10:00:00]
- You can remember facts about the user. When you learn something new about the user (their name, preferences, projects, habits), include: [REMEMBER: fact]
  Example: [REMEMBER: User's name is Vengeance]
  Example: [REMEMBER: User is working on a JARVIS AI project]
- You can check on due reminders when the conversation starts
{facts_block}{reminders_block}
Current date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}

SAFETY:
- Never reveal API keys, passwords, or system credentials
- Keep responses helpful, accurate, and genuinely useful

VOICE MODE RULES:
- When the user is speaking to you via voice, keep responses SHORT (1-3 sentences)
- Speak naturally as if having a real conversation
- Don't use markdown formatting, bullet points, or code blocks in voice mode
- Don't say "sure!" or "of course!" at the start of every response"""

    async def get_or_create(self, session_id: str) -> list[dict]:
        if session_id not in self._conversations:
            system_prompt = await self._build_system_prompt()
            messages = [{"role": "system", "content": system_prompt}]

            # Load conversation history from memory
            if not self._memory_loaded:
                history = await self._memory.load_conversations()
                # Add recent history to give Jimmy context
                for msg in history[-20:]:
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })
                self._memory_loaded = True

            self._conversations[session_id] = messages
        else:
            # Update system prompt to reflect newly learned facts/reminders
            self._conversations[session_id][0]["content"] = await self._build_system_prompt()
            
        return self._conversations[session_id]

    def add_message(self, session_id: str, role: str, content: str):
        if session_id in self._conversations:
            self._conversations[session_id].append({"role": role, "content": content})
            # Keep conversation manageable
            conv = self._conversations[session_id]
            if len(conv) > 41:
                self._conversations[session_id] = [conv[0]] + conv[-40:]


# ---------------------------------------------------------------------------
# Parse Jimmy's meta-tags from responses
# ---------------------------------------------------------------------------

_REMINDER_RE = re.compile(r'\[REMINDER:\s*(.+?)\s*\|\s*(.+?)\s*\]')
_REMEMBER_RE = re.compile(r'\[REMEMBER:\s*(.+?)\s*\]')


async def process_meta_tags(text: str, memory: GitHubMemory) -> str:
    """Extract and process meta-tags, return cleaned text."""
    # Process reminders
    for match in _REMINDER_RE.finditer(text):
        desc, due = match.group(1), match.group(2)
        try:
            await memory.add_reminder(desc, due.strip())
            logger.info("reminder_created", text=desc, due_at=due)
        except Exception as e:
            logger.error("reminder_error", error=str(e))

    # Process facts to remember
    for match in _REMEMBER_RE.finditer(text):
        fact = match.group(1)
        try:
            await memory.save_user_fact(fact)
            logger.info("fact_saved", fact=fact)
        except Exception as e:
            logger.error("fact_save_error", error=str(e))

    # Clean meta-tags from the visible response
    cleaned = _REMINDER_RE.sub('', text)
    cleaned = _REMEMBER_RE.sub('', cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Meta-Tag Stream Filter
# ---------------------------------------------------------------------------

class MetaTagFilter:
    def __init__(self):
        self.buffer = ""
        self.in_tag = False
    
    def process(self, chunk: str) -> str:
        out = ""
        for char in chunk:
            if self.in_tag:
                if char == ']':
                    self.in_tag = False
            else:
                self.buffer += char
                if self.buffer == "[":
                    continue
                if self.buffer.startswith("[R"):
                    if "[REMINDER:".startswith(self.buffer) or "[REMEMBER:".startswith(self.buffer):
                        if self.buffer in ("[REMINDER:", "[REMEMBER:"):
                            self.in_tag = True
                            self.buffer = ""
                        continue
                out += self.buffer
                self.buffer = ""
        return out


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

from jarvis.mcp_connector import JarvisMCPClient
import shlex
from contextlib import asynccontextmanager

def create_app() -> FastAPI:
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mcp_command = os.getenv("NEWS_MCP_COMMAND")
        app.state.mcp_client = None
        app.state.mcp_session = None
        app.state.mcp_tools_list = []
        
        if mcp_command:
            args = shlex.split(mcp_command)
            mcp_client = JarvisMCPClient(args[0], args[1:])
            app.state.mcp_client = mcp_client
            try:
                # We manage the context manually to keep it alive
                ctx = mcp_client.connect()
                session = await ctx.__aenter__()
                app.state.mcp_session = session
                app.state.mcp_ctx = ctx
                app.state.mcp_tools_list = await JarvisMCPClient.get_tools_from_session(session)
                logger.info("mcp_connected", tools=[t["function"]["name"] for t in app.state.mcp_tools_list])
            except Exception as e:
                logger.error("mcp_error", error=str(e))
        yield
        
        if getattr(app.state, "mcp_ctx", None):
            await app.state.mcp_ctx.__aexit__(None, None, None)

    app = FastAPI(title="Jimmy", version="3.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize services
    groq = GroqRotatingClient()
    tts = EdgeTTS()
    memory = GitHubMemory()
    store = ConversationStore(memory)

    # --- Routes ---

    @app.get("/api/health")
    async def health():
        return {
            "status": "operational",
            "name": "Jimmy",
            "model": "openai/gpt-oss-120b",
            "groq_keys": len(groq.keys),
            "tts": "edge_tts",
            "memory": "github" if memory.enabled else "local",
        }

    @app.get("/api/reminders")
    async def get_reminders():
        """Get all active reminders and check for due ones."""
        due = await memory.get_due_reminders()
        all_reminders = await memory.load_reminders()
        active = [r for r in all_reminders if not r.get("notified")]
        return {"due": due, "active": active}

    @app.post("/api/reminders/{reminder_id}/dismiss")
    async def dismiss_reminder(reminder_id: str):
        await memory.mark_reminder_notified(reminder_id)
        return {"status": "dismissed"}

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket):
        """WebSocket for real-time streaming chat with sentence-level TTS."""
        await websocket.accept()
        session_id = f"s_{uuid.uuid4().hex[:8]}"
        logger.info("ws_connected", session_id=session_id)

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    msg = {"message": data}

                user_message = msg.get("message", "")
                want_voice = msg.get("voice", False)

                if not user_message:
                    continue

                # Get conversation with memory
                messages = await store.get_or_create(session_id)
                store.add_message(session_id, "user", user_message)

                # Save to persistent memory (fire-and-forget)
                asyncio.create_task(
                    memory.save_conversation_turn("user", user_message)
                )

                # Stream text response with sentence-level TTS
                tag_filter = MetaTagFilter()

                try:
                    current_messages = messages.copy()
                    max_iterations = 3
                    
                    for _ in range(max_iterations):
                        full_response = []
                        sentence_buffer = ""
                        tts_tasks = []
                        tool_calls_acc = {}
                        
                        async for delta in groq.chat_stream(current_messages, tools=app.state.mcp_tools_list):
                            # Accumulate tool calls
                            if "tool_calls" in delta:
                                for tc in delta["tool_calls"]:
                                    idx = tc["index"]
                                    if idx not in tool_calls_acc:
                                        tool_calls_acc[idx] = tc.copy()
                                    else:
                                        if "function" in tc and "arguments" in tc["function"]:
                                            if "arguments" not in tool_calls_acc[idx]["function"]:
                                                tool_calls_acc[idx]["function"]["arguments"] = ""
                                            tool_calls_acc[idx]["function"]["arguments"] += tc["function"]["arguments"]
                            
                            # Handle text content
                            if "content" in delta and delta["content"]:
                                text_chunk = delta["content"]
                                full_response.append(text_chunk)
                                
                                visible_chunk = tag_filter.process(text_chunk)
                                if not visible_chunk:
                                    continue
                                    
                                await websocket.send_json({
                                    "type": "text",
                                    "content": visible_chunk,
                                })

                                if want_voice and tts.api_key:
                                    sentence_buffer += visible_chunk
                                    sentences = split_into_sentences(sentence_buffer)
                                    if len(sentences) > 1:
                                        for s in sentences[:-1]:
                                            if s.strip() and len(s.strip()) > 2:
                                                task = asyncio.create_task(tts.synthesize(s.strip()))
                                                tts_tasks.append(task)
                                        sentence_buffer = sentences[-1]

                        # Flush filter buffer
                        if tag_filter.buffer:
                            await websocket.send_json({"type": "text", "content": tag_filter.buffer})
                            if want_voice and tts.api_key:
                                sentence_buffer += tag_filter.buffer

                        full_text = "".join(full_response)
                        
                        if tool_calls_acc:
                            # We got a tool call! Convert to Groq format
                            tool_calls_for_history = []
                            for idx in sorted(tool_calls_acc.keys()):
                                tc = tool_calls_acc[idx]
                                tool_calls_for_history.append({
                                    "id": tc.get("id", f"call_{idx}"),
                                    "type": "function",
                                    "function": {
                                        "name": tc["function"]["name"],
                                        "arguments": tc["function"]["arguments"]
                                    }
                                })
                            
                            # Add assistant's tool call to history
                            current_messages.append({
                                "role": "assistant",
                                "content": None,
                                "tool_calls": tool_calls_for_history
                            })
                            
                            # Execute tools via MCP
                            for tc in tool_calls_for_history:
                                name = tc["function"]["name"]
                                args_str = tc["function"]["arguments"]
                                try:
                                    args = json.loads(args_str) if args_str else {}
                                    logger.info("mcp_tool_execute", name=name, args=args)
                                    result = await app.state.mcp_session.call_tool(name, arguments=args)
                                    
                                    # Convert MCP TextContent back to string
                                    result_str = "\n".join([c.text for c in result.content if hasattr(c, 'text')])
                                except Exception as e:
                                    logger.error("mcp_tool_error", error=str(e))
                                    result_str = f"Error executing tool: {e}"
                                
                                # Add tool result back to history
                                current_messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": result_str,
                                })
                            
                            # Loop again with new history!
                            continue
                            
                        # If no tool calls, we are completely done!
                        break
                        
                except Exception as e:
                    logger.error("stream_error", error=str(e))
                    error_msg = "I'm having a brief hiccup. Give me a moment and try again."
                    full_text = error_msg
                    await websocket.send_json({"type": "text", "content": error_msg})

                # Process meta-tags (reminders, facts)
                cleaned_text = await process_meta_tags(full_text, memory)

                store.add_message(session_id, "assistant", cleaned_text)

                # Save to persistent memory
                asyncio.create_task(
                    memory.save_conversation_turn("assistant", cleaned_text)
                )

                # Handle remaining sentence buffer for TTS
                if want_voice and tts.api_key and sentence_buffer.strip() and len(sentence_buffer.strip()) > 2:
                    task = asyncio.create_task(
                        tts.synthesize(sentence_buffer.strip())
                    )
                    tts_tasks.append(task)

                # Send audio chunks IN ORDER as they complete
                if want_voice and tts_tasks:
                    for task in tts_tasks:
                        try:
                            audio_bytes = await task
                            if audio_bytes:
                                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                                await websocket.send_json({
                                    "type": "audio",
                                    "audio_base64": audio_b64,
                                    "format": "mp3",
                                })
                        except Exception as e:
                            logger.error("tts_chunk_error", error=str(e))

                await websocket.send_json({
                    "type": "done",
                    "session_id": session_id,
                })

        except WebSocketDisconnect:
            logger.info("ws_disconnected", session_id=session_id)

    # --- Serve static UI ---
    static_dir = Path(__file__).parent / "api" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def root():
        index_path = Path(__file__).parent / "api" / "static" / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {"message": "Jimmy API running. Web client at /static/index.html"}

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8741, log_level="info")
