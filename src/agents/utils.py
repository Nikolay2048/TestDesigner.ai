from pprint import pprint


import json
import time
from functools import wraps
from pprint import pprint

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.constants import END, START
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode


def log_tool_execution(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        print("\n" + "=" * 100)
        print(f"[TOOL START] {func.__name__}")

        #
        # INPUT ARGS
        #
        if args:
            print("\nPOSITIONAL ARGS:")
            pprint(args, width=120)

        if kwargs:
            print("\nKEYWORD ARGS:")
            pprint(kwargs, width=120)

        started_at = time.time()

        try:
            #
            # EXECUTE TOOL
            #
            result = func(*args, **kwargs)

            duration = round(time.time() - started_at, 3)

            #
            # RESULT
            #
            print("\nRESULT:")

            if isinstance(result, (dict, list)):
                pprint(result, width=120)
            else:
                print(result)

            print(f"\nEXECUTION TIME: {duration}s")

            print(f"[TOOL END] {func.__name__}")
            print("=" * 100)

            return result

        except Exception as e:
            duration = round(time.time() - started_at, 3)

            print("\nERROR:")
            print(str(e))

            print(f"\nEXECUTION TIME: {duration}s")

            print("\n" + "=" * 100)
            print(f"[TOOL FAILED] {func.__name__}")
            print("=" * 100)

            raise

    return wrapper

def check_llm_connection(llm):
    # Connection check
    test_response = llm.invoke('Reply with one word: working?')
    print(f'Test: {test_response.content}')
    print('Setup complete!')


def print_agent_trace(result: dict) -> None:
    print("\n" + "=" * 80)
    print("AGENT TRACE")
    print("=" * 80)

    messages = result.get("messages", [])

    for index, message in enumerate(messages, start=1):
        print(f"\n[{index}] MESSAGE TYPE: {message.__class__.__name__}")

        if hasattr(message, "content"):
            print(f"CONTENT:\n{message.content}")

        if hasattr(message, "tool_calls") and message.tool_calls:
            print("\nTOOL CALLS:")

            for tool_call in message.tool_calls:
                print(f"  TOOL NAME: {tool_call.get('name')}")
                print(f"  TOOL ARGS:")
                pprint(tool_call.get("args"))

        if hasattr(message, "name"):
            print(f"\nTOOL NAME: {message.name}")

    print("\n" + "=" * 80)

def run_and_trace(agent, query: str):
    """Runs the agent and displays the TAO loop step by step."""
    print(f'User: {query}')
    print('=' * 60)

    start_time = time.time()
    result = agent.invoke({'messages': [HumanMessage(content=query)]})
    elapsed = time.time() - start_time

    tao_steps = []
    tool_count = 0
    step_num = 0
    for msg in result['messages']:
        msg_type = type(msg).__name__

        if msg_type == 'AIMessage' and hasattr(msg, 'tool_calls') and msg.tool_calls:
            step_num += 1
            for tc in msg.tool_calls:
                tool_count += 1
                print(f'\n--- TAO Step {step_num} ---')
                if msg.content:
                    print(f'  THOUGHT: {msg.content[:200]}')
                print(f'  ACTION:  {tc["name"]}({json.dumps(tc["args"])})')
            tao_steps.append(step_num)

        elif msg_type == 'ToolMessage':
            content_preview = msg.content[:250] if len(msg.content) > 250 else msg.content
            print(f'  OBSERVE: {content_preview}')

        elif msg_type == 'AIMessage' and not getattr(msg, 'tool_calls', None):
            if msg.content and msg != result['messages'][0]:
                print('\n--- Final Answer ---')
                print(f'  {msg.content[:1500]}')

    print(f'\n{"=" * 60}')
    print(f'TAO cycles: {step_num} | Tool calls: {tool_count} | Time: {elapsed:.2f}s')

    return result, tao_steps


def create_react_agent(llm, tools_list=None, system_prompt=None):
    """Creates a ReAct agent with the given tools.

    Includes an explicit THOUGHT step: if the model calls a tool
    without writing reasoning text (common with function-calling),
    we make an extra LLM call to extract the reasoning.
    """

    # parallel_tool_calls=False — forces the model to call one tool per step,
    # making the TAO loop explicit: Thought → Action → Observation → Thought → ...
    llm_with_tools = llm.bind_tools(tools_list)

    def agent_node(state: MessagesState):
        messages = state['messages']
        # Add system prompt if not present yet
        if not any(isinstance(msg, SystemMessage) for msg in messages):
            messages = [SystemMessage(content=system_prompt)] + messages
        response = llm_with_tools.invoke(messages)

        # if response.tool_calls and not response.content:
        #     tool_info = ', '.join(tc['name'] for tc in response.tool_calls)
        #     thought = llm.invoke(
        #         messages
        #         + [
        #             HumanMessage(
        #                 content=f'You chose to call: {tool_info}. '
        #                         'In 1 sentence, explain why this is the right next step. '
        #                         'Reply with ONLY your reasoning, no tool calls.'
        #             )
        #         ]
        #     )
        #     response.content = thought.content

        return {'messages': [response]}

    def should_continue(state: MessagesState):
        last_message = state['messages'][-1]
        if last_message.tool_calls:
            return 'tools'
        return END

    # Build the graph
    workflow = StateGraph(MessagesState)
    workflow.add_node('agent', agent_node)
    workflow.add_node('tools', ToolNode(tools_list))

    workflow.add_edge(START, 'agent')
    workflow.add_conditional_edges('agent', should_continue, {'tools': 'tools', END: END})
    workflow.add_edge('tools', 'agent')

    return workflow.compile()