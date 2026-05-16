from pprint import pprint


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