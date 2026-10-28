


import os
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
client = OpenAI(base_url=os.getenv("API_URL"), api_key=os.getenv("API_KEY"))


def model_properties(m):
    """
    Get all model fields as a dict.
    """

    if hasattr(m, "model_dump"):
        return m.model_dump()
    
    return dict(getattr(m, "__dict__", {}))


def format_value(value, indent):
    """
    Nest lists and dicts with custom format.
    """

    pad = " " * indent
    if isinstance(value, dict):
        width = max((len(str(k)) for k in value), default=0)
        lines = [f"{pad}{str(k):<{width}} : {v}" for k, v in value.items()]
        return "\n" + "\n".join(lines)
    
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = [f"{pad}[{i}]{format_value(item, indent + 4)}" for i, item in enumerate(value)]
        return "\n" + "\n".join(lines)
    
    return str(value)



if __name__ == "__main__":
    models = client.models.list().data
    print(f"Tilgjengelige modeller: {len(models)}\n")

    for m in models:
        props = model_properties(m)
        name = props.get("id", "ukjent")

        # Increase the box size to fit long model names
        box_width = max(len(name) + 2, 40)
        print("+" + "-" * box_width + "+")
        print("| " + name.ljust(box_width - 1) + "|")
        print("+" + "-" * box_width + "+")

        # Align all property keys to the same column
        key_width = max(len(str(k)) for k in props)
        for key, value in props.items():
            print(f"    {str(key):<{key_width}} : {format_value(value, 8)}")
        print()
