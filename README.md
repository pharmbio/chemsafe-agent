# ChemSafeAgent - An AI Agent for Chemical Safety

## Overview

Chemical safety assessment requires reasoning over heterogeneous data — molecular structures, regulatory Standard Operating Procedures (SOPs), toxicological endpoints, and scientific literature — while keeping every safety-relevant decision traceable to an authoritative source. ChemSafeAgent is a **multi-agent system** that autonomously plans, executes, and summarizes chemical safety workflows under **human-in-the-loop supervision**. It combines a LangGraph orchestration graph, a restricted Python execution environment, retrieval-augmented access to professional SOPs, and a library of domain skills (cheminformatics, database traversal, weight-of-evidence reasoning) so that any chemical, threshold, or exposure decision is grounded in a verifiable source or explicitly flagged as unverified.

<div align="center">
  <img src="images/agent_illustration.png" width="700">
</div>

### Core Agent Architecture

- **Task Classifier**: An LLM routes each request into `simple`, `complex`, or `meta_query`, choosing the appropriate execution path.
- **Planning Agent**: Decomposes complex tasks into a step-by-step plan, drawing on the relevant SOPs and skills before any execution begins.
- **Human Approval (Human-in-the-Loop)**: Plans are never auto-approved — the graph pauses for plan review and only proceeds once the user's free-text feedback is judged as an approval.
- **Execute Agent (CodeAct)**: A ReAct agent that carries out the work by writing and running code, reading files, and loading domain skills on demand.
- **Summary Agent**: Synthesizes results into a grounded, source-attributed report.
- **Critics Agent** *(under development)*: Will review the Execute Agent's intermediate work and provide corrective feedback in a critique loop before results are summarized.

### Tool Surface & Skills

- **`python_executor`**: The primary work engine. Code runs through a custom **restricted interpreter** (not raw `exec`), with only an allow-listed set of imports (RDKit, pandas, admet-ai, DeepChem, requests, and the repo's own modules). State persists across calls within a conversation.
- **`read_files`**: Reads repo files, skill playbooks, and scoped artifacts, all behind a strict path sandbox.
- **Domain Skills**: Markdown playbooks (plus optional helper scripts) the agent loads on demand — `data_inspection`, `database_traversal` (ECHA/PubChem/NIOSH/OPCW APIs), `data_visualization`, `sop_search`, `literature_search`, `woe_reasoning` (weight-of-evidence), and `cheminformatics` (RDKit + QSAR/ADMET).

### Grounding & Memory Systems

- **SOP RAG**: An ensemble retriever (BM25 sparse + Chroma dense, fused) over professional Standard Operating Procedures, so safety thresholds and requirements are cited from source documents.
- **Persistent Conversations**: LangGraph state is checkpointed in PostgreSQL via an auto-reconnecting pool, making conversations resumable and human-approval interrupts durable.
- **Context Compression**: A rolling summary plus structured memory (facts / outputs / decisions / open questions) bounds token growth over long sessions.
- **Scoped Persistence & Auth**: Uploads, outputs, and state are scoped per `(user, conversation)`, with argon2-based authentication and registration-requires-approval.

## Quick Start

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- OpenAI API key from [platform.openai.com](https://platform.openai.com/)
- A [Supabase](https://supabase.com/) project — used as the PostgreSQL backend for LangGraph checkpointing and user authentication (copy its connection string)
- The prebuilt **memory folder** (SOP RAG indexes and agent memory), downloaded separately — see step 2 below
- (Optional) LangSmith account for tracing from [smith.langchain.com](https://smith.langchain.com/)

### Initial Setup

```bash
# 1. Clone repository
git clone https://github.com/your-username/chemsafe-agent.git
cd chemsafe-agent

# 2. Download the prebuilt memory folder and place it under persistence/
#    Download memory.zip (~535 MB) from Google Drive:
#      https://drive.google.com/file/d/1F0Bd4RCfBk8LgaGrby4QE3J3DBSd2bls/view?usp=share_link
#    Then unzip it so that the folder lives at persistence/memory
unzip ~/Downloads/memory.zip -d persistence/
#    After this step, `persistence/memory/` should exist.

# 3. Create a .env file with the required credentials
## Mandatory: OpenAI API key
echo "OPENAI_API_KEY=your-openai-api-key-here" > .env

## Mandatory: Supabase PostgreSQL connection string
echo "DATABASE_URL=postgresql://postgres:your-password@your-project.supabase.co:5432/postgres" >> .env

## Optional: LangSmith tracing
echo "LANGSMITH_TRACING=true" >> .env
echo "LANGSMITH_ENDPOINT=https://api.smith.langchain.com" >> .env
echo "LANGSMITH_API_KEY=your-langsmith-api-key-here" >> .env
echo "LANGSMITH_PROJECT=chemsafe-agent" >> .env

# 4. Build the Docker image
docker build --platform linux/amd64 -t chemsafeagent:trial .

# 5. Run the container (passing your .env)
docker run --rm -it --env-file .env -p 7860:7860 chemsafeagent:trial
```

Open [http://localhost:7860](http://localhost:7860) to access the Gradio application.

### Daily Usage

```bash
# Start the application (after initial setup)
docker run --rm -it --env-file .env -p 7860:7860 chemsafeagent:trial

# Stop the application
# Press Ctrl+C in the terminal running the container
```

### LangSmith Setup (Optional)
1. Create an account at [smith.langchain.com](https://smith.langchain.com/)
2. Get your API key from the settings page
3. Add the `LANGSMITH_*` variables to your `.env` file as shown above
4. Rebuild/restart the container to apply the changes

## Project Structure
```
chemsafe-agent/
├── app/            # Gradio UI, streaming, auth, and file-download routes
├── core/           # Agent graph, prompts, tools, and domain skills
├── backend/        # Persistence, path sandbox, auth, and SOP RAG
├── persistence/    # Scoped data, results, and memory roots
├── images/         # Diagrams and logos
├── Dockerfile      # Self-contained build (pre-caches the RAG embedding model)
├── requirements.txt
└── main.py         # Entry point (launches the Gradio app)
```
