

import os 
from pathlib import Path
from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI


env_path = Path(__file__).resolve().parent.parent / ".env.local"
load_dotenv(env_path)


model_config = [os.getenv("API_MODEL"), os.getenv("API_URL"), os.getenv("API_KEY")]


llm = ChatOpenAI(
    model    = model_config[0],
    base_url = model_config[1],
    api_key  = model_config[2]
)


agent = create_agent(
    model = llm,
    tools=[],
    system_prompt="You are a helpful assistant"
)


result = agent.invoke(
    {
        "messages":
        [{
            "role": "user",
            "content": "whats 2+2?"
        }]
    }
)


print(result["messages"][-1].content_blocks)