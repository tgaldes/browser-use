"""
Simple try of the agent.

@dev You need to add ANTHROPIC_API_KEY to your environment variables.
"""

import logging
import os
import sys

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import asyncio

from browser_use import Agent
from browser_use.controller.service import Controller


def get_llm():
    return ChatOpenAI(model='gpt-4o', temperature=0.3)


parser = argparse.ArgumentParser()
parser.add_argument('query', type=str, help='The query to process')

args = parser.parse_args()

llm = get_llm()

agent = Agent(
	task=args.query,
	llm=llm,
	controller=Controller(keep_open=True),
	# save_conversation_path='./tmp/try_flight/',
)


async def main():
	await agent.run()


asyncio.run(main())
