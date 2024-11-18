import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from langchain_openai import ChatOpenAI

from browser_use import Agent
from browser_use.controller.service import Controller

"""
Example: Using the 'Return text' action.

This script demonstrates how a controller can be created with a function that handles text returned from the agent.

"""

llm = ChatOpenAI(model='gpt-4o')

def handle_output(text):
	print(f'Printing text from agent in handle_output: {text}')


agent = Agent(
	task="Navigate to 'https://en.wikipedia.org/wiki/Internet' and return the text of a single sentence on the page. Then you're done.",
	llm=llm,
	controller=Controller(output_func=handle_output),
)


async def main():
	await agent.run()


if __name__ == '__main__':
	asyncio.run(main())

