from pprint import pprint


import json
import time
from functools import wraps
from pprint import pprint


def log_tool_execution(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        print("\n" + "=" * 100)
        print(f"[TOOL START] {func.__name__}")
        print("=" * 100)

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

            print("\n" + "=" * 100)
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