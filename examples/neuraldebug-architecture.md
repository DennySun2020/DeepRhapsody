# NeuralDebug: Architecture Diagrams

These diagrams illustrate how the **NeuralDebug** AI agent drives real debuggers (GDB, LLDB, CDB) to find bugs end-to-end.

---

## 1. End-to-End Architecture

Shows all 6 layers: Developer → NeuralDebug Agent → TCP Client → TCP Server → Debugger Backend → Target Program.

```mermaid
%%{ init: { 'flowchart': { 'nodeSpacing': 70, 'rankSpacing': 70 } } }%%
flowchart TB
    subgraph DEV["👤 Developer"]
        D1["'Debug my_program.c — it crashes on the second call'"]
        D2["'Put a breakpoint at funcA(), print the callstack and check all variables'"]
        D1 ~~~ D2
    end

    subgraph PILOT["🤖 NeuralDebug Agent (AI Reasoning Layer)"]
        direction TB
        P1["1. Read & Understand Source Code"]
        P2["2. Detect Platform & Toolchain (info → JSON report)"]
        P2b["3. Discover Repo Build System (repo-context → build system, docs, source dirs, test dirs)"]
        P2c["4. Locate Target Binary (find-binary → search build dirs)"]
        P3["5. Auto-Compile with Debug Symbols (compile → MSVC/GCC/Clang)"]
        P4["6. Launch TCP Debug Server (serve → background process) OR: serve --attach PID OR: serve --core dump"]
        P5["7. Set Strategic Breakpoints (cmd b function / file:line)"]
        P6["8. Start Execution (cmd start)"]
        P7["9. Analyze JSON Response (location, variables, stack)"]
        P8["10. Explain Findings to Developer"]
        P9["11. Decide Next Action (step/continue/inspect/evaluate)"]
        P10["12. Report Root Cause & Suggest Fix"]

        P1 --> P2 --> P2b --> P2c --> P3 --> P4 --> P5 --> P6 --> P7 --> P8
        P8 --> P9 --> P7
        P8 --> P10
    end

    subgraph CLIENT["📟 TCP Client - Skills"]
        C1["Open TCP socket → 127.0.0.1:5678"]
        C2["Send JSON command {'action':'step_over','args':''}"]
        C3["Receive JSON response"]
        C4["Close socket & exit"]
        C1 --> C2 --> C3 --> C4
    end

    subgraph SERVER["🖥️ TCP Debug Server"]
        S1["Accept connection"]
        S2["Parse JSON command"]
        S3["Dispatch to debugger backend"]
        S4["Build JSON response"]
        S5["Send response || close connection"]
        S1 --> S2 --> S3 --> S4 --> S5
        S5 -.->|"wait for next connection"| S1
    end

    subgraph BACKEND["⚙️ Debugger"]
        direction TB
        B1["GdbDebugger"]
        B2["LldbDebugger"]
        B3["CdbDebugger"]
    end

    subgraph TARGET["🎯 Target Program"]
        T1["Compiled Binary with Debug Symbol"]
    end

    DEV -->|"natural language"| PILOT
    PILOT -->|"terminal command: cmd --port 5678 step_over"| CLIENT
    CLIENT -->|"TCP JSON"| SERVER
    SERVER -->|"debugger commands: -exec-next/next/p"| BACKEND
    BACKEND -->|"control subprocess"| TARGET
    TARGET -->|"breakpoint hit/signal"| BACKEND
    BACKEND -->|"events"| SERVER
    SERVER -->|"JSON response"| CLIENT
    CLIENT -->|"stdout JSON"| PILOT
    PILOT -->|"plain language explanation"| DEV

    style DEV fill:#E8F5E9,stroke:#2E7D32,stroke-width:2px,color:#1B5E20
    style PILOT fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1
    style CLIENT fill:#FFF3E0,stroke:#E65100,stroke-width:2px,color:#BF360C
    style SERVER fill:#F3E5F5,stroke:#6A1B9A,stroke-width:2px,color:#4A148C
    style BACKEND fill:#FCE4EC,stroke:#B71C1C,stroke-width:2px,color:#880E4F
    style TARGET fill:#FFFDE7,stroke:#F57F17,stroke-width:2px,color:#E65100
```

---

## 2. Complete Debugging Session — Sequence Diagram

A real end-to-end session in 4 phases: Setup → Breakpoints & Start → Interactive Investigation → Root Cause & Fix.

```mermaid
sequenceDiagram
    actor Dev as 👤 Developer
    participant Agent as 🤖 NeuralDebug
    participant Term as 💻 Terminal
    participant Client as 📟 TCP Client
    participant Server as 🖥️ TCP Server
    participant DBG as ⚙️ GDB/LLDB/CDB
    participant Prog as 🎯 Program

    Note over Dev,Prog: Phase 1 — Setup & Launch
    Dev->>Agent: "Debug crash.c — segfault on line 42"
    Agent->>Agent: Read source code, understand structure
    Agent->>Term: py cpp_debug_session.py info
    Term-->>Agent: JSON: {compilers: [msvc], debuggers: [cdb], recommend: msvc+cdb}
    Agent->>Term: py cpp_debug_session.py repo-context --repo .
    Term-->>Agent: JSON: {build_system: cmake, doc_files: [...], source_dirs: [...]}
    Agent->>Term: py cpp_debug_session.py serve crash.c --port 5678 (background)

    Note over Server,Prog: Server auto-compiles crash.c → crash.exe
    activate Server
    Server->>DBG: Launch: cdb -lines -o crash.exe
    activate DBG
    DBG->>Prog: Create process (suspended)
    activate Prog
    DBG-->>Server: System breakpoint hit (ready)
    Server-->>Term: "Debug server listening on port 5678"

    Note over Dev,Prog: Phase 2 — Breakpoints & Start
    Agent->>Term: py ... cmd --port 5678 b process_data
    Term->>Client: spawn one-shot process
    activate Client
    Client->>Server: TCP → {"action":"set_breakpoint","args":"process_data"}
    Server->>DBG: bp process_data
    DBG-->>Server: Breakpoint 0 set
    Server-->>Client: {"status":"paused","message":"Breakpoint set at process_data"}
    Client-->>Term: print JSON
    deactivate Client
    Term-->>Agent: JSON response

    Agent->>Term: py ... cmd --port 5678 start
    activate Client
    Client->>Server: TCP → {"action":"start","args":""}
    Server->>DBG: g (go / continue)
    DBG->>Prog: Resume execution
    Prog->>DBG: Hit breakpoint at process_data()
    DBG-->>Server: Stopped at crash.c:38, process_data()
    Server->>Server: Get stack (kn), locals (dv /t), location
    Server-->>Client: {"status":"paused","current_location":{...},"local_variables":{...}}
    Client-->>Term: print JSON
    deactivate Client
    Term-->>Agent: JSON response
    Agent-->>Dev: "Paused at process_data() line 38. Args: buf=0x7ff.., size=1024"

    Note over Dev,Prog: Phase 3 — Interactive Investigation
    Dev->>Agent: "Step through and check the pointer"

    loop Agent reasoning loop
        Agent->>Agent: Decide: step_over to inspect variables
        Agent->>Term: py ... cmd --port 5678 step_over
        activate Client
        Client->>Server: TCP → {"action":"step_over","args":""}
        Server->>DBG: p (step over)
        DBG->>Prog: Execute one line
        Prog-->>DBG: Stopped at crash.c:39
        DBG-->>Server: Location + variables
        Server-->>Client: JSON response
        Client-->>Term: print JSON
        deactivate Client
        Agent->>Agent: Analyze: ptr is NULL! 🚨
        Agent-->>Dev: "Line 39: ptr = get_buffer() returned NULL!"
    end

    Note over Dev,Prog: Phase 4 — Root Cause & Fix
    Agent->>Term: py ... cmd --port 5678 evaluate "ptr == NULL"
    activate Client
    Client->>Server: TCP → {"action":"evaluate","args":"ptr == NULL"}
    Server->>DBG: ?? ptr == NULL
    DBG-->>Server: true (1)
    Server-->>Client: {"eval_result":{"value":"1"}}
    Client-->>Term: print JSON
    deactivate Client

    Agent-->>Dev: "🐛 Bug found: get_buffer() returns NULL when size > 512. Fix: add null check before line 42."

    Agent->>Term: py ... cmd --port 5678 quit
    activate Client
    Client->>Server: TCP → {"action":"quit","args":""}
    Server->>DBG: q (quit)
    DBG->>Prog: Terminate
    deactivate Prog
    deactivate DBG
    Server-->>Client: {"status":"completed"}
    deactivate Server
    deactivate Client

    Note over Dev,Prog: Session saved to .NeuralDebug/debug-summary.md
```

---

## 3. Single Command Data Flow

Traces one command ("Step into that function") through every layer from natural language to debugger-native command and back.

```mermaid
flowchart LR
    subgraph INPUT["Developer Intent"]
        A["'Step into that function'"]
    end

    subgraph AGENT["NeuralDebug AI Reasoning"]
        B["Map to: step_in"]
        C["Run terminal: py ... cmd step_in"]
    end

    subgraph TRANSPORT["TCP Transport Layer"]
        D["Client opens socket → 127.0.0.1:5678"]
        E["Send JSON: {'action':'step_in'}"]
        F["Server accepts & parses"]
    end

    subgraph DISPATCH["CppDebugServer._dispatch()"]
        G{"action?"}
        G1["step_in → cmd_step_in()"]
        G2["step_over → cmd_step_over()"]
        G3["continue → cmd_continue()"]
        G4["inspect → cmd_inspect()"]
        G5["evaluate → cmd_evaluate()"]
        G6["b → cmd_set_breakpoint()"]
    end

    subgraph BACKENDS["Debugger Backend (polymorphic)"]
        direction TB
        H1["GdbDebugger send MI: -exec-step parse: *stopped"]
        H2["LldbDebugger send: step parse: regex on output"]
        H3["CdbDebugger send: t parse: prompt + location"]
    end

    subgraph RESPONSE["Unified JSON Response"]
        I["{   status: 'paused',   current_location: {file, line, func},   call_stack: [...],   local_variables: {x: '42'},   message: 'Stepped into foo()' }"]
    end

    subgraph OUTPUT["Developer Sees"]
        J["'We stepped into foo() at line 25. x=42, ptr=0x7ff...'"]
    end

    A --> B --> C --> D --> E --> F --> G
    G --> G1
    G --> G2
    G --> G3
    G --> G4
    G --> G5
    G --> G6
    G1 --> H1
    G1 --> H2
    G1 --> H3
    H1 --> I
    H2 --> I
    H3 --> I
    I --> J

    style INPUT fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    style AGENT fill:#E3F2FD,stroke:#1565C0,color:#0D47A1
    style TRANSPORT fill:#FFF3E0,stroke:#E65100,color:#BF360C
    style DISPATCH fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    style BACKENDS fill:#FCE4EC,stroke:#B71C1C,color:#880E4F
    style RESPONSE fill:#E0F7FA,stroke:#00695C,color:#004D40
    style OUTPUT fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **TCP server/client split** | Copilot agents can only run terminal commands — TCP lets a stateless CLI control a persistent debugger subprocess |
| **One connection per command** | Simple, no state leaks, easy error recovery — if a command fails the next one still works |
| **Polymorphic backends** | `GdbDebugger`, `LldbDebugger`, `CdbDebugger` share the same `cmd_*()` interface — the server doesn't know which is running |
| **Unified JSON schema** | Every command returns the same fields (`status`, `current_location`, `call_stack`, `local_variables`) regardless of backend |
| **Auto-compile** | Developer hands a `.c` file to the agent — the system compiles with debug symbols automatically before launching the debugger |
| **Platform auto-detection** | `ToolchainInfo` searches PATH, Visual Studio directories, Windows SDK, and xcrun to find the best tools for the current OS |
| **CDB preferred on Windows** | CDB natively reads PDB symbols from MSVC — no format mismatch, works with the same debug engine as Visual Studio and WinDbg |
| **Repo discovery** | `build-info`, `find-binary`, `repo-context` let the agent understand unfamiliar projects — detect build system, locate binaries, find docs |
| **Attach mode** | `--attach PID` enables debugging running processes — essential for deadlocks, hangs, and production issues |
| **Core dump mode** | `--core PATH` enables post-mortem crash analysis — no need to reproduce the bug |
| **Auto source path mapping** | Server auto-detects repo root via `.git` and adds it as a source path — source-level debugging works even for out-of-tree builds |
