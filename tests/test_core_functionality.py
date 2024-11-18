import asyncio

import pytest
from langchain_openai import ChatOpenAI

from browser_use.agent.service import Agent
from browser_use.controller.service import Controller


@pytest.fixture
def llm():
	"""Initialize language model for testing"""
	return ChatOpenAI(model='gpt-4o')  # Use appropriate model


@pytest.fixture
async def controller():
	"""Initialize the controller"""
	controller = Controller()
	try:
		yield controller
	finally:
		if controller.browser:
			controller.browser.close(force=True)


@pytest.mark.asyncio
async def test_search_google(llm, controller):
	"""Test 'Search Google' action"""
	agent = Agent(
		task="Search Google for 'OpenAI'.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=2)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'search_google' in action_names


@pytest.mark.asyncio
async def test_go_to_url(llm, controller):
	"""Test 'Navigate to URL' action"""
	agent = Agent(
		task="Navigate to 'https://www.python.org'.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=2)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'go_to_url' in action_names


@pytest.mark.asyncio
async def test_go_back(llm, controller):
	"""Test 'Go back' action"""
	agent = Agent(
		task="Go to 'https://www.example.com', then go back.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=3)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'go_to_url' in action_names
	assert 'go_back' in action_names


@pytest.mark.asyncio
async def test_click_element(llm, controller):
	"""Test 'Click element' action"""
	agent = Agent(
		task="Go to 'https://www.python.org' and click on the first link.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=4)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'go_to_url' in action_names
	assert 'click_element' in action_names


@pytest.mark.asyncio
async def test_input_text(llm, controller):
	"""Test 'Input text' action"""
	agent = Agent(
		task="Go to 'https://www.google.com' and input 'OpenAI' into the search box.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=4)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'go_to_url' in action_names
	assert 'input_text' in action_names


@pytest.mark.asyncio
async def test_switch_tab(llm, controller):
	"""Test 'Switch tab' action"""
	agent = Agent(
		task="Open new tabs with 'https://www.google.com' and 'https://www.wikipedia.org', then switch to the first tab.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=6)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	open_tab_count = action_names.count('open_tab')
	assert open_tab_count >= 2
	assert 'switch_tab' in action_names


@pytest.mark.asyncio
async def test_open_new_tab(llm, controller):
	"""Test 'Open new tab' action"""
	agent = Agent(
		task="Open a new tab and go to 'https://www.example.com'.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=3)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'open_tab' in action_names


@pytest.mark.asyncio
async def test_extract_page_content(llm, controller):
	"""Test 'Extract page content' action"""
	agent = Agent(
		task="Go to 'https://www.example.com' and extract the page content.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=3)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'go_to_url' in action_names
	assert 'extract_content' in action_names


@pytest.mark.asyncio
async def test_done_action(llm, controller):
	"""Test 'Complete task' action"""
	agent = Agent(
		task="Navigate to 'https://www.example.com' and signal that the task is done.",
		llm=llm,
		controller=controller,
	)
	history = await agent.run(max_steps=3)
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'go_to_url' in action_names
	assert 'done' in action_names


# run with: pytest -k test_scroll_down
@pytest.mark.asyncio
async def test_scroll_down(llm, controller):
	"""Test 'Scroll down' action and validate that the page actually scrolled"""
	agent = Agent(
		task="Go to 'https://en.wikipedia.org/wiki/Internet' and scroll down the page.",
		llm=llm,
		controller=controller,
	)
	# Get the browser instance
	browser = controller.browser
	driver = browser._get_driver()

	# Navigate to the page and get initial scroll position
	await agent.run(max_steps=1)
	initial_scroll_position = driver.execute_script('return window.pageYOffset;')

	# Perform the scroll down action
	await agent.run(max_steps=2)
	final_scroll_position = driver.execute_script('return window.pageYOffset;')

	# Validate that the scroll position has changed
	assert final_scroll_position > initial_scroll_position, 'Page did not scroll down'

	# Validate that the 'scroll_down' action was executed
	history = agent.history
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'scroll_down' in action_names

# run with pytest -k test_return_text
@pytest.mark.asyncio
async def test_return_text(llm, controller):
	"""Test 'Return text' action and validate that desired text is returned."""

	returned_text = ''
	def handle_ouput(text):
		nonlocal returned_text
		returned_text = text

	controller.set_output_func(handle_ouput)

	agent = Agent(
		task="Go to 'https://en.wikipedia.org/wiki/Internet'. Return the text of the first sentence that uses the phrase 'constituent network'",
		llm=llm,
		controller=controller,
	)

	history = await agent.run(max_steps=3)

	# Validate that the 'return_text' action was executed
	actions = [h.model_output.action for h in history if h.model_output and h.model_output.action]
	action_names = [list(action.model_dump(exclude_unset=True).keys())[0] for action in actions]
	assert 'return_text' in action_names

	# Validate that the desired text was returned
	assert returned_text, 'No text was returned'
	assert 'constituent network' in returned_text, 'Desired text was not returned'
