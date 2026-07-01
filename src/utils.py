


def _get_message(result):
    """
    Accept either the full {"messages": [...]} dict or a bare message.
    """
    return result["messages"][-1] if isinstance(result, dict) else result


def print_output(result):
    """
    Print the answer content and whether it completed cleanly.
    """

    msg = _get_message(result)
    meta = getattr(msg, "response_metadata", {}) or {}
    content = (getattr(msg, "content", "") or "").replace("\n", " ")
    print("\n  OUTPUT")
    print(f"    content       : {content}")
    print(f"    finish_reason : {meta.get('finish_reason')}")


def print_tokens(result):
    """
    Print token usage for cost and context-budget accounting.
    """

    msg = _get_message(result)
    usage = getattr(msg, "usage_metadata", {}) or {}
    print("\n  TOKENS")
    print(f"    input  : {usage.get('input_tokens')}")
    print(f"    output : {usage.get('output_tokens')}")
    print(f"    total  : {usage.get('total_tokens')}")


def print_tools(result):
    """
    Print the tools the agent invoked.
    """

    msg = _get_message(result)
    tool_calls = getattr(msg, "tool_calls", []) or []
    print("\n  TOOLS")
    print(f"    tool_calls : {[t.get('name') for t in tool_calls]}")


def print_model(result):
    """
    Print model provenance and the exact server build.
    """

    msg = _get_message(result)
    meta = getattr(msg, "response_metadata", {}) or {}
    print("\n  MODEL")
    print(f"    model_name         : {meta.get('model_name')}")
    print(f"    provider           : {meta.get('model_provider')}")
    print(f"    system_fingerprint : {meta.get('system_fingerprint')}")
    print(f"    response_id        : {meta.get('id')}")


def print_response_info(result, show_output=True, show_tokens=True, show_tools=False, show_model=False):
    """
    Print selected sections of an agent result. Toggle each with its flag.
    """
    
    width = 44
    print("+" + "=" * width + "+")
    print("| " + "RESPONSE SUMMARY".ljust(width - 1) + "|")
    print("+" + "=" * width + "+")

    if show_output:
        print_output(result)
    if show_tokens:
        print_tokens(result)
    if show_tools:
        print_tools(result)
    if show_model:
        print_model(result)
    print()