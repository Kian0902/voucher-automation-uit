from langchain_core.runnables.graph import CurveStyle

def save_graph_png(app, output_path="agent_graph.png"):
    """
    Render a compiled LangGraph graph as a PNG image file.
    """

    png_bytes = app.get_graph().draw_mermaid_png(curve_style=CurveStyle.BASIS)

    with open(output_path, "wb") as png_file:
        png_file.write(png_bytes)

    return output_path



def print_invoice_problems(result, source_file=None):
    """
    Print the labeled problems of one invoice, one block per problem.

    Example:
        Faktura 209384.xml
        - [Class 1]: buyer_reference is a person name
            - Field: buyer_reference
            - Value: <person name>
            - Why:   A person name is not an 8-digit koststed.
    """

    if source_file is not None:
        print(f"\n{source_file}")

    labeled = result["labeled_problems"]
    if labeled is None:
        print("No problems identified.")
        return

    for problem in labeled.problems:
        print(f"- [Class {problem.label}]: {problem.problem}")
        print(f"    - Field: {problem.field}")
        print(f"    - Value: {problem.value}")
        print(f"    - Why:   {problem.why}")
        print("\n")


def print_code_hierarchy(codes, root_label="UiT"):
    """
    Print a code dictionary as an ASCII tree, like a folder listing.

    Args:
        codes (dict): {code: {"short_name": str | None, "name": str,
                      "parent": str | None}} - e.g. STEDKODER from
                      stedkoder.py. Entries with parent None are top level.
        root_label (str): Text for the root line. Defaults to "UiT".

    Example:
        UiT
        ├── 2611 [ADM] Rektorat og universitetsledelse
        │   ├── 261101 Universitetsledelsen
        │   └── 261111 Rektors stab
        └── 32 [NT-FAK] Fakultet for naturvitenskap og teknologi
            └── 3214 [IFI] Institutt for informatikk
    """

    # Group every code under its parent (None = top level), sorted
    children = {}
    for code, info in codes.items():
        children.setdefault(info["parent"], []).append(code)
    for child_list in children.values():
        child_list.sort()

    def print_subtree(code, prefix, is_last):
        info = codes[code]
        short = f" [{info['short_name']}]" if info["short_name"] else ""
        print(prefix + ("└── " if is_last else "├── ") + code + short + " " + info["name"])

        # Children continue with a pipe if this branch continues below
        child_prefix = prefix + ("    " if is_last else "│   ")
        child_codes = children.get(code, [])
        for index, child_code in enumerate(child_codes):
            print_subtree(child_code, child_prefix, index == len(child_codes) - 1)

    print(root_label)
    top_codes = children.get(None, [])
    for index, top_code in enumerate(top_codes):
        print_subtree(top_code, "", index == len(top_codes) - 1)


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