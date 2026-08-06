# Project Context & Session Rules for multi-agent-research

## Project Architecture
- **Framework**: FastAPI backend REST service orchestrated by **LangGraph**.
- **Agents**:
  - **Researcher**: Decomposes topics into search queries, executes searches via **Tavily API**, synthesizes notes.
  - **Analyst**: Extracts key themes, notable insights, and missing gaps from notes.
  - **Writer**: Drafts initial reports and rewrites drafts based on human reviewer feedback.
- **Human-In-The-Loop (HITL)**: Asynchronous review workflow using LangGraph `interrupt()`.
- **State Checkpointing**: Persistent SQLite checkpointer (`SqliteSaver`) configured via `DB_PATH = checkpoints.sqlite` in `app/config.py`.
- **API Models**: `ResearchResponse` surfaces `sub_queries` to inspect researcher search queries.
- **Documentation**: Root endpoint (`GET /`) automatically redirects to Swagger UI docs (`/docs`).

## Development & Test Commands
- Start dev server: `uv run uvicorn app.main:app --reload`
- Run test suite: `uv run pytest`
- Lint code: `uv run ruff check .`

## Transcripts & History Location
- Conversation transcripts are saved locally at: `C:\Users\psaik\.gemini\antigravity-ide\brain\<conversation-id>\.system_generated\logs\`
