# Tutorial: Using NeuralDebug with LangChain / Open-Source Agents

This guide shows how to use NeuralDebug with LangChain, AutoGen, CrewAI,
or any Python-based AI agent framework.

These frameworks naturally support both modes:
- **Autonomous mode**: use a ReAct or function-calling agent that loops until done
- **Interactive mode**: call tools directly from your code, one at a time

## Prerequisites

- Python 3.8+
- Your AI framework (`pip install langchain` or similar)
- A debugger for your target language

## LangChain

```python
from integrations.langchain.tools import get_NeuralDebug_tools

# Get framework-agnostic tools
tools = get_NeuralDebug_tools()

# Convert to LangChain tools
lc_tools = [t.to_langchain() for t in tools]

# Use with any LangChain agent
from langchain.agents import initialize_agent
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4", temperature=0)
agent = initialize_agent(lc_tools, llm, agent="structured-chat-zero-shot-react-description")

result = agent.run("Debug /path/to/main.py — the sort function returns wrong results")
print(result)
```

## Direct Python Usage (No Framework)

```python
from integrations.langchain.tools import get_NeuralDebug_tools

tools = get_NeuralDebug_tools()

# Call tools directly
for t in tools:
    print(f"{t.name}: {t.description}")

# Start a debug session
result = tools[1].func(target="main.py", port=5678)  # NeuralDebug_start_server
print(result)

# Send commands
cmd_tool = tools[3]  # NeuralDebug_command
print(cmd_tool.func(command="b", args="42", port=5678))
print(cmd_tool.func(command="start", port=5678))
print(cmd_tool.func(command="inspect", port=5678))
print(cmd_tool.func(command="backtrace", port=5678))

# Stop
tools[4].func(port=5678)  # NeuralDebug_stop
```

## AutoGen

```python
from autogen import AssistantAgent, UserProxyAgent
from integrations.langchain.tools import get_NeuralDebug_tools

tools = get_NeuralDebug_tools()

# Register tools with AutoGen
assistant = AssistantAgent("debugger", llm_config={...})
user_proxy = UserProxyAgent("user", human_input_mode="NEVER")

for tool in tools:
    @user_proxy.register_for_execution()
    @assistant.register_for_llm(description=tool.description)
    def debug_func(tool=tool, **kwargs):
        return tool.func(**kwargs)

user_proxy.initiate_chat(assistant, message="Debug main.c — segfault on line 42")
```

## CrewAI

```python
from crewai import Agent, Task, Crew
from integrations.langchain.tools import get_NeuralDebug_tools

lc_tools = [t.to_langchain() for t in get_NeuralDebug_tools()]

debugger = Agent(
    role="AI Debugger",
    goal="Find and explain bugs in code",
    tools=lc_tools,
    llm="gpt-4"
)

task = Task(
    description="Debug main.py — the average calculation is wrong",
    agent=debugger
)

crew = Crew(agents=[debugger], tasks=[task])
result = crew.kickoff()
```

## Custom Agent (No Framework)

For agents that just need shell access, use the universal system prompt:

```python
# Read the prompt
with open("prompts/universal.md") as f:
    system_prompt = f.read()

# Replace <path-to-NeuralDebug> with actual path
system_prompt = system_prompt.replace(
    "<path-to-NeuralDebug>",
    "/path/to/NeuralDebug"
)

# Use as your agent's system prompt with any LLM
```

## Tips

- `get_NeuralDebug_tools()` returns 5 high-level tools (info, start, status, command, stop)
- The `NeuralDebug_command` tool accepts any debug command (b, step_over, inspect, etc.)
- Set `scripts_dir` parameter to override script location
- Tools are stateless wrappers — all state lives in the debug server process
