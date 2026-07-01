

import os 
from pathlib import Path
from dotenv import load_dotenv
from utils import print_response_info


from langchain.agents import create_agent
from langchain_openai import ChatOpenAI


env_path = Path(__file__).resolve().parent.parent / ".env.local"
load_dotenv(env_path)


class Agent:
    def __init__(self, system_prompt: str, tools: list | None=None):
        """
        Construct an agent backed by the UiT-hosted LLM.

        Builds the chat model and a LangChain agent once at construction,
        so the agent can be reused across multiple invocations.

        Args:
            system_prompt: Instructions defining the agent's behaviour and role.
            tools: Optional list of tools the agent may call. Defaults to
                an empty list (no tools) when not provided.
        """

        # Define agent identity and tools
        self.system_prompt = system_prompt
        self.tools = tools if tools is not None else []
        self._output_response = None
        
        # Define LLM
        self._llm = ChatOpenAI(
            model=os.getenv("API_MODEL"),
            base_url=os.getenv("API_URL"),
            api_key=os.getenv("API_KEY"),
        )

        # Define langchain agent
        self._agent = create_agent(
            model = self._llm,
            tools=self.tools,
            system_prompt = self.system_prompt
        )



    def send_message(self, input_prompt: str ="") -> None:

        self._output_response = self._agent.invoke(
            {
                "messages":[{"role": "user", "content": f"{input_prompt}"}]
            }
        )
        
    

    def get_answer(self):
        """
        Get only the Agent's answer
        """

        x = self._output_response
        if x is None:
            return None
        return x["messages"][-1].content


    def get_response(self):
        """
        Get full Agent response
        """

        if self._output_response is None:
            return None
        return self._output_response


ai = Agent(system_prompt="You are a helpful assistant.")
ai.send_message(input_prompt="whats you name?")

# output = ai.get_answer()
# print(output)

output = ai.get_response()
print_response_info(output)