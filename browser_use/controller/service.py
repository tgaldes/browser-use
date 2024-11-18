import logging

from main_content_extractor import MainContentExtractor
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from browser_use.agent.views import ActionModel, ActionResult
from browser_use.browser.service import Browser
from browser_use.browser.views import TabInfo
from browser_use.controller.registry.service import Registry
from browser_use.controller.views import (
	ClickElementAction,
	DoneAction,
	ExtractPageContentAction,
	GoToUrlAction,
	InputTextAction,
	OpenTabAction,
	ScrollDownAction,
	SearchGoogleAction,
	SwitchTabAction,
	ReturnTextAction,
)
from browser_use.utils import time_execution_sync
import pdb

logger = logging.getLogger(__name__)


class Controller:
	def __init__(self, keep_open: bool = False, output_func: callable = None):
		self.browser = Browser(keep_open=keep_open)
		self.registry = Registry()
		self._register_default_actions()
		self._output_func = output_func

	def set_output_func(self, output_func: callable):
		self._output_func = output_func

	def _register_default_actions(self):
		"""Register all default browser actions"""

		# Basic Navigation Actions
		@self.registry.action(
			'Search Google', param_model=SearchGoogleAction, requires_browser=True
		)
		def search_google(params: SearchGoogleAction, browser: Browser):
			driver = browser._get_driver()
			driver.get(f'https://www.google.com/search?q={params.query}')
			browser.wait_for_page_load()

		@self.registry.action('Navigate to URL', param_model=GoToUrlAction, requires_browser=True)
		def go_to_url(params: GoToUrlAction, browser: Browser):
			driver = browser._get_driver()
			driver.get(params.url)
			browser.wait_for_page_load()

		@self.registry.action('Go back', requires_browser=True)
		def go_back(browser: Browser):
			driver = browser._get_driver()
			driver.back()
			browser.wait_for_page_load()

		# Element Interaction Actions
		@self.registry.action(
			'Click element', param_model=ClickElementAction, requires_browser=True
		)
		def click_element(params: ClickElementAction, browser: Browser):
			state = browser._cached_state
			#import pdb; pdb.set_trace()

			if params.index not in state.selector_map:
				print(state.selector_map)
				raise Exception(
					f'Element with index {params.index} does not exist - retry or use alternative actions'
				)

			xpath = state.selector_map[params.index]
			driver = browser._get_driver()
			initial_handles = len(driver.window_handles)

			msg = None

			for _ in range(params.num_clicks):
				try:
					browser._click_element_by_xpath(xpath)
					msg = f'🖱️  Clicked element {params.index}: {xpath}'
					if params.num_clicks > 1:
						msg += f' ({_ + 1}/{params.num_clicks} clicks)'
				except Exception as e:
					logger.warning(f'Element no longer available after {_ + 1} clicks: {str(e)}')
					break

			if len(driver.window_handles) > initial_handles:
				browser.handle_new_tab()

			return ActionResult(extracted_content=f'Clicked element {msg}')

		@self.registry.action('Input text', param_model=InputTextAction, requires_browser=True)
		def input_text(params: InputTextAction, browser: Browser):
			state = browser._cached_state
			if params.index not in state.selector_map:
				raise Exception(
					f'Element index {params.index} does not exist - retry or use alternative actions'
				)

			xpath = state.selector_map[params.index]
			browser._input_text_by_xpath(xpath, params.text)
			msg = f'⌨️  Input text "{params.text}" into element {params.index}: {xpath}'
			return ActionResult(extracted_content=msg)

		# Tab Management Actions
		@self.registry.action('Switch tab', param_model=SwitchTabAction, requires_browser=True)
		def switch_tab(params: SwitchTabAction, browser: Browser):
			driver = browser._get_driver()

			# Verify handle exists
			if params.handle not in driver.window_handles:
				raise ValueError(f'Tab handle {params.handle} not found')

			# Only switch if we're not already on that tab
			if params.handle != driver.current_window_handle:
				driver.switch_to.window(params.handle)
				browser._current_handle = params.handle
				# Wait for tab to be ready
				browser.wait_for_page_load()

			# Update and return tab info
			tab_info = TabInfo(handle=params.handle, url=driver.current_url, title=driver.title)
			browser._tab_cache[params.handle] = tab_info

		@self.registry.action('Open new tab', param_model=OpenTabAction, requires_browser=True)
		def open_tab(params: OpenTabAction, browser: Browser):
			driver = browser._get_driver()
			driver.execute_script(f'window.open("{params.url}", "_blank");')
			browser.wait_for_page_load()
			browser.handle_new_tab()

		# Content Actions
		@self.registry.action(
			'Extract page content', param_model=ExtractPageContentAction, requires_browser=True
		)
		def extract_content(params: ExtractPageContentAction, browser: Browser):
			driver = browser._get_driver()

			content = MainContentExtractor.extract(  # type: ignore
				html=driver.page_source,
				output_format=params.value,
			)
			return ActionResult(extracted_content=content)

		@self.registry.action('Complete task', param_model=DoneAction, requires_browser=True)
		def done(params: DoneAction, browser: Browser):
			logger.info(f'✅ Done on page {browser._cached_state.url}\n\n: {params.text}')
			return ActionResult(is_done=True, extracted_content=params.text)

		@self.registry.action(
			'Scroll down the page by pixel amount - if no amount is specified, scroll down one page',
			param_model=ScrollDownAction,
			requires_browser=True,
		)
		def scroll_down(params: ScrollDownAction, browser: Browser):
			driver = browser._get_driver()
			if params.amount is not None:
				driver.execute_script(f'window.scrollBy(0, {params.amount});')
			else:
				body = driver.find_element(By.TAG_NAME, 'body')
				body.send_keys(Keys.PAGE_DOWN)

		# scroll up
		@self.registry.action(
			'Scroll up the page by pixel amount',
			param_model=ScrollDownAction,
			requires_browser=True,
		)
		def scroll_up(params: ScrollDownAction, browser: Browser):
			driver = browser._get_driver()
			driver.execute_script(f'window.scrollBy(0, -{params.amount});')

		# Register an action that passes text back to the user
		@self.registry.action(
			'Return text',
			param_model=ReturnTextAction, # TODO
			requires_browser=False,
		)
		def return_text(params: ReturnTextAction):
			if self._output_func is not None:
				self._output_func(params.text)
			else:
				logger.warning(f'No output function provided, returned text ignored.')
			


	def action(self, description: str, **kwargs):
		"""Decorator for registering custom actions

		@param description: Describe the LLM what the function does (better description == better function calling)
		"""
		return self.registry.action(description, **kwargs)

	@time_execution_sync('--act')
	def act(self, action: ActionModel) -> ActionResult:
		"""Execute an action"""
		try:
			for action_name, params in action.model_dump(exclude_unset=True).items():
				if params is not None:
					result = self.registry.execute_action(action_name, params, browser=self.browser)
					if isinstance(result, str):
						return ActionResult(extracted_content=result)
					elif isinstance(result, ActionResult):
						return result
					elif result is None:
						return ActionResult()
					else:
						raise ValueError(f'Invalid action result type: {type(result)} of {result}')
			return ActionResult()
		except Exception as e:
			raise e
