# Message Handling — Architecture

## Overview

The bot receives Discord messages via the `on_message` event handler and **enqueues them immediately** into a priority queue. A background async processor loop drains the queue and processes messages serially. All LLM calls, tool use, and replies happen in the processor loop — the event handler never blocks.

Messages are stored in per-channel history with timestamps and usernames. The bot is aware of the current date and time. All actions are logged at the configured level. Configuration is stored in `config.yaml`.

## Components

| Component | File | Role |
|-----------|------|------|
| `on_message` | `run.py` | Discord event handler — fast enqueue only |
| `MessageQueue` | `engine/message_queue.py` | Priority queue: mentions=0, auto-response=1 |
| `_processor_loop` | `run.py` | Background loop: drain queue → process messages |
| `process_message` | `run.py` | Core pipeline: skills → history → LLM → reply |
| `evaluate_should_respond` | `run.py` | Decides whether to auto-join a conversation (includes history context) |
| `call_ollama` | `run.py` | Calls the LLM with tool-calling support (up to 5 tool rounds) |
| `execute_mcp_tool` | `run.py` | Dispatches tool calls to MCP servers, timer engine, or memory engine |
| `update_skills` | `run.py` | Auto-matches message content to skills; resets on mention |
| `build_prompt` | `run.py` | Assembles system + skill + history messages for the LLM |
| `cleanup_history` | `run.py` | Prunes expired and excess messages from channel history |

---

## Flow Diagrams

### 1. Message Entry Point

```mermaid
flowchart TD
    A[Discord Message Arrives] --> B{Author is Bot?}
    B -- Yes --> C[Ignore]
    B -- No --> D{Bot Mentioned?}
    D -- Yes --> E[Strip Mention]
    D -- No --> F[Keep Raw Content]
    E --> G{Content Empty?}
    F --> G
    G -- Yes --> H[Ignore]
    G -- No --> I[Track Active User]
    I --> J{Is Mention?}
    J -- Yes --> K[Enqueue is_mention=true priority 0]
    J -- No --> L{Auto-response Enabled?}
    L -- No --> M[Store in history only]
    L -- Yes --> N[Enqueue is_mention=false priority 1]
    K --> O[Return immediately]
    N --> O
    M --> O
```

### 2. Processor Loop

```mermaid
flowchart TD
    A[Processor Loop] --> B[Dequeue with timeout 0.5s]
    B --> C{Timeout?}
    C -- Yes --> A
    C -- No --> D{Is Mention?}
    D -- Yes --> E[process_message directly]
    D -- No --> F[evaluate_should_respond]
    F --> G{Should respond?}
    G -- Yes --> E
    G -- No --> H[Append to history only]
    E --> I[update_skills]
    I --> J[cleanup_history]
    J --> K[Append user message]
    K --> L[build_prompt]
    L --> M["Show typing indicator"]
    M --> N[call_ollama with tools]
    N --> O{LLM wants tool call?}
    O -- Yes --> P[execute_mcp_tool]
    P --> Q[Append tool result]
    Q --> N
    O -- No --> R[Get final response]
    R --> S[Append assistant response]
    S --> T[save_history]
    T --> U[channel.send]
    U --> A
    H --> A
```

### 3. Tool Dispatch Routing

```mermaid
flowchart LR
    A[execute_mcp_tool] --> B{tool_name}
    B -- add/list/cancel_reminder --> C[Timer Engine]
    B -- remember/recall/forget_memory --> D[Memory Engine]
    B -- tavily_search / fetch --> E[MCP Server Session]
    B -- unknown --> F[Error: tool not found]

    style C fill:#bbf
    style D fill:#bfb
    style E fill:#fbb
```

### 4. Auto-response Evaluation

```mermaid
flowchart TD
    A[evaluate_should_respond] --> B{Message too short?}
    B -- Yes --> C[Return False]
    B -- No --> D{User recently active?}
    D -- Yes --> E[Return True - skip LLM call]
    D -- No --> F[Build eval prompt with last 6 messages]
    F --> G[LLM: should bot respond?]
    G --> H{LLM says YES?}
    H -- Yes --> E
    H -- No --> C

    style E fill:#bfb
    style C fill:#fbb
    style F fill:#bbf
```

### 5. Startup Sequence

```mermaid
sequenceDiagram
    participant Main
    participant MCP
    participant Timer
    participant Memory
    participant Processor
    participant Discord

    Main->>MCP: setup_mcp()
    MCP->>MCP: Start MCP servers (tavily, fetch)
    MCP->>MCP: Register timer + memory tools
    Main->>Timer: engine.load(), start_monitor()
    Main->>Memory: engine.load()
    Main->>Processor: asyncio.create_task(_processor_loop)
    Main->>Discord: client.start(token)
    Discord->>Discord: on_ready → load skills, register slash commands
    Discord->>Discord: on_message begins enqueuing messages
```

---

## Data Flow (State)

```mermaid
flowchart TD
    subgraph on_message-handler
        filter[Filter self, empty]
        extract[Extract content, mention]
        track[Track active user]
    end

    subgraph Processor-loop-serial
        dequeue[Dequeue message]
        skills[update_skills]
        hist[cleanup + append history]
        prompt[build_prompt]
        llm[call_ollama]
        tools[execute_mcp_tool]
        send[channel.send]
    end

    subgraph Shared-state-protected
        H[history dict<br/>keyed by guild_id_channel_id]
        AS[active_skills dict<br/>keyed by channel_id]
        AU[active_users dict<br/>keyed by channel_id]
    end

    subgraph Persisted
        DH[data/history/*.json]
        DR[data/reminders.json]
        DM[data/memories.json]
    end

    on_message-handler --> Q[MessageQueue]
    Q --> Processor-loop-serial
    Processor-loop-serial --> Shared-state-protected
    Processor-loop-serial --> Persisted

    style Q fill:#bbf
    style Shared-state-protected fill:#bfb
```

---

## Key Design Decisions

### 1. Priority Queue (mentions first)

`MessageQueue` wraps a `heapq`-backed list with priority 0 for mentions and priority 1 for auto-response. This ensures the bot always responds to direct mentions before evaluating channel messages.

### 2. Single processor loop (no locking needed)

The processor loop runs single-threaded in the asyncio event loop, so access to shared state (history, active_skills, active_users) is naturally serialized. No locks required.

### 3. Bounded queue (drops lowest priority)

Default max size is 50. When the queue is full, the message with the highest priority number (auto-response) and oldest timestamp is dropped. Mentions are never dropped while auto-response messages are queued.

### 4. No global mutable author_id

`author_id` is passed as a parameter through the call chain: `process_message(msg: Message) → call_ollama(prompt, msg.author_id) → execute_mcp_tool(name, args, author_id)`. This eliminates shared mutable state between message processing calls.

### 5. Eval prompt includes conversation context

`evaluate_should_respond` includes the last 6 messages from the channel's history in the eval prompt, so the LLM can judge whether responding makes sense in context.

---

## Prior Art — Resolved Issues

All issues from the original design have been resolved:

| Issue | Resolution |
|-------|-----------|
| Blocking event loop | `on_message` only enqueues; processor loop handles all blocking work |
| Global `_current_author_id` | Removed; author_id passed through call chain |
| Race conditions on shared state | Single processor loop serializes all state mutations |
| Redundant skill matching | `update_skills` is the single point of skill matching |
| Eval prompt missing context | Includes last 6 messages from channel history |
| Active-user shortcut broken | Tracks users in both `on_message` and processor loop for consistency |
