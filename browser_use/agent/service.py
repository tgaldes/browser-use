from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Optional, TypeVar

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from openai import RateLimitError
from pydantic import BaseModel, ValidationError

from browser_use.agent.prompts import AgentMessagePrompt, AgentSystemPrompt
from browser_use.agent.views import (
	ActionResult,
	AgentError,
	AgentHistory,
	AgentOutput,
	ModelPricingCatalog,
	Pricing,
	TokenDetails,
	TokenUsage,
)
from browser_use.browser.views import BrowserState
from browser_use.controller.service import Controller
from browser_use.utils import time_execution_async

load_dotenv()
logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


class Agent:
	def __init__(
		self,
		task: str,
		llm: BaseChatModel,
		controller: Optional[Controller] = None,
		use_vision: bool = True,
		save_conversation_path: Optional[str] = None,
		max_failures: int = 5,
		retry_delay: int = 10,
	):
		self.task = task
		self.use_vision = use_vision
		self.llm = llm
		self.save_conversation_path = save_conversation_path

		# Controller setup
		self.controller_injected = controller is not None
		self.controller = controller or Controller()

		# Action and output models setup
		self._setup_action_models()

		# Message history setup
		self.messages = self._initialize_messages()

		# Tracking variables
		self.history: list[AgentHistory] = []
		self.n_steps = 1
		self.consecutive_failures = 0
		self.max_failures = max_failures
		self.retry_delay = retry_delay

		if save_conversation_path:
			logger.info(f'Saving conversation to {save_conversation_path}')

		self.usage_metadata = TokenUsage(
			input_tokens=0,
			output_tokens=0,
			total_tokens=0,
			input_token_details=TokenDetails(),
			output_token_details=TokenDetails(),
		)

	def _setup_action_models(self) -> None:
		"""Setup dynamic action models from controller's registry"""
		# Get the dynamic action model from controller's registry
		self.ActionModel = self.controller.registry.create_action_model()
		# Create output model with the dynamic actions
		self.AgentOutput = AgentOutput.type_with_custom_actions(self.ActionModel)

	def _initialize_messages(self) -> list[BaseMessage]:
		"""Initialize message history with system and first message"""
		# Get action descriptions from controller's registry
		action_descriptions = self.controller.registry.get_prompt_description()

		system_prompt = AgentSystemPrompt(
			self.task, action_description=action_descriptions, current_date=datetime.now()
		).get_system_message()

		first_message = HumanMessage(content=f'Your task is: {self.task}')
		return [system_prompt, first_message]

	@time_execution_async('--step')
	async def step(self) -> None:
		"""Execute one step of the task"""
		logger.info(f'\n📍 Step {self.n_steps}')
		#import pdb; pdb.set_trace()
		state = self.controller.browser.get_state(use_vision=self.use_vision)

		try:
			model_output = await self.get_next_action(state)
			result = self.controller.act(model_output.action)
			if result.extracted_content:
				logger.info(f'📄 Result: {result.extracted_content}')
			self.consecutive_failures = 0

		except Exception as e:
			result = self._handle_step_error(e, state)
			model_output = None
		self._update_messages_with_result(result)
		self._make_history_item(model_output, state, result)

	def _handle_step_error(self, error: Exception, state: BrowserState) -> ActionResult:
		"""Handle all types of errors that can occur during a step"""
		error_msg = AgentError.format_error(error)
		prefix = f'❌ Result failed {self.consecutive_failures + 1}/{self.max_failures} times:\n '

		if isinstance(error, (ValidationError, ValueError)):
			logger.error(f'{prefix}{error_msg}')
			self.consecutive_failures += 1
		elif isinstance(error, RateLimitError):
			logger.warning(f'{prefix}{error_msg}')
			time.sleep(self.retry_delay)
			self.consecutive_failures += 1
		else:
			logger.error(f'{prefix}{error_msg}')
			self.consecutive_failures += 1

		return ActionResult(error=error_msg)

	def _update_messages_with_result(self, result: ActionResult) -> None:
		"""Update message history with action results"""
		if result.extracted_content:
			self.messages.append(HumanMessage(content=result.extracted_content))
		if result.error:
			self.messages.append(HumanMessage(content=result.error))

	def _make_history_item(
		self,
		model_output: AgentOutput | None,
		state: BrowserState,
		result: ActionResult,
	) -> None:
		"""Create and store history item"""
		history_item = AgentHistory(model_output=model_output, result=result, state=state)
		self.history.append(history_item)

	@time_execution_async('--get_next_action')
	async def get_next_action(self, state: BrowserState) -> AgentOutput:
		"""Get next action from LLM based on current state"""
		#import pdb; pdb.set_trace()
		new_message = AgentMessagePrompt(state).get_user_message()
		input_messages = self.messages + [new_message]

		structured_llm = self.llm.with_structured_output(self.AgentOutput, include_raw=True)
		response: dict[str, Any] = await structured_llm.ainvoke(input_messages)  # type: ignore

		parsed: AgentOutput = response['parsed']

		self._update_message_history(state, parsed)
		self._log_response(parsed)
		self._save_conversation(input_messages, parsed)
		self._update_usage_metadata(response['raw'])
		return parsed

	def _calc_token_cost(self) -> float:
		"""
		Calculate the cost of tokens used in a request based on the model.

		:param usage_metadata: TokenUsage model containing token usage details.
		:param model_name: The name of the model used.
		:return: Cost of the tokens used.
		"""
		if isinstance(self.llm, ChatOpenAI):
			model_name = self.llm.model_name
		elif isinstance(self.llm, ChatAnthropic):
			model_name = self.llm.model
		else:
			logger.debug('Model name not supported for pricing calculation')
			return 0

		pricing_catalog = ModelPricingCatalog()

		if model_name == 'gpt-4o':
			model_pricing = pricing_catalog.gpt_4o
		elif model_name == 'gpt-4o-mini':
			model_pricing = pricing_catalog.gpt_4o_mini
		elif model_name == 'claude-3-5-sonnet-20240620':
			model_pricing = pricing_catalog.claude_3_5_sonnet
		else:
			logger.debug(f'Unsupported model: {model_name}')
			return 0

		uncached_input_tokens = (
			self.usage_metadata.input_tokens - self.usage_metadata.input_token_details.cache_read
		)
		factor = 1e6
		cost = (
			(uncached_input_tokens / factor) * model_pricing.uncached_input
			+ (self.usage_metadata.input_token_details.cache_read / factor)
			* model_pricing.cached_input
			+ (self.usage_metadata.output_tokens / factor) * model_pricing.output
		)
		return cost

	def _update_usage_metadata(self, raw_response: AIMessage) -> None:
		"""
		Process the response and update usage.

		:param raw_response: The response object containing usage metadata.
		"""
		# only supported for openai models for now
		if isinstance(self.llm, ChatAnthropic):
			token_usage_data: dict[str, Any] = raw_response.response_metadata['usage']
			usage_metadata = TokenUsage(
				input_tokens=token_usage_data.get('input_tokens', 0),
				output_tokens=token_usage_data.get('output_tokens', 0),
				total_tokens=token_usage_data.get('input_tokens', 0)
				+ token_usage_data.get('output_tokens', 0),
			)
			self.usage_metadata.input_tokens += usage_metadata.input_tokens
			self.usage_metadata.output_tokens += usage_metadata.output_tokens
			self.usage_metadata.total_tokens += usage_metadata.total_tokens

		elif isinstance(self.llm, ChatOpenAI):
			token_usage_data: dict[str, Any] = raw_response.response_metadata['token_usage']
			usage_metadata = TokenUsage(
				input_tokens=token_usage_data.get('prompt_tokens', 0),
				output_tokens=token_usage_data.get('completion_tokens', 0),
				total_tokens=token_usage_data.get('total_tokens', 0),
				input_token_details=TokenDetails(
					audio=token_usage_data.get('prompt_tokens_details', {}).get('audio_tokens', 0),
					cache_read=token_usage_data.get('prompt_tokens_details', {}).get(
						'cached_tokens', 0
					),
					reasoning=0,  # Assuming reasoning is not part of prompt_tokens_details
				),
				output_token_details=TokenDetails(
					audio=token_usage_data.get('completion_tokens_details', {}).get(
						'audio_tokens', 0
					),
					cache_read=0,  # Assuming cache_read is not part of completion_tokens_details
					reasoning=token_usage_data.get('completion_tokens_details', {}).get(
						'reasoning_tokens', 0
					),
				),
			)

			self.usage_metadata.input_tokens += usage_metadata.input_tokens
			self.usage_metadata.output_tokens += usage_metadata.output_tokens
			self.usage_metadata.total_tokens += usage_metadata.total_tokens

			# update usage metadata
			if usage_metadata.input_token_details:
				for detail_key in usage_metadata.input_token_details.model_dump():
					setattr(
						self.usage_metadata.input_token_details,
						detail_key,
						getattr(self.usage_metadata.input_token_details, detail_key)
						+ getattr(usage_metadata.input_token_details, detail_key),
					)

			if usage_metadata.output_token_details:
				for detail_key in usage_metadata.output_token_details.model_dump():
					setattr(
						self.usage_metadata.output_token_details,
						detail_key,
						getattr(self.usage_metadata.output_token_details, detail_key)
						+ getattr(usage_metadata.output_token_details, detail_key),
					)

		else:
			logger.debug('Model name not supported for pricing calculation')
			return

		self._log_usage_metadata(usage_metadata)

	def _log_usage_metadata(self, current_tokens: Optional[TokenUsage] = None) -> None:
		"""Log the usage metadata"""
		total_cost = self._calc_token_cost()
		total_tokens = self.usage_metadata.total_tokens
		logger.debug(
			f'🔢 Total Tokens: input: {self.usage_metadata.input_tokens} (cached: {self.usage_metadata.input_token_details.cache_read}) + output: {self.usage_metadata.output_tokens} = {total_tokens} = ${total_cost:.4f} 💰'
		)

		if current_tokens:
			logger.debug(
				f'🔢 Last  Tokens: input: {current_tokens.input_tokens} (cached: {current_tokens.input_token_details.cache_read}) + output: {current_tokens.output_tokens} = {current_tokens.total_tokens} '
			)

	def _update_message_history(self, state: BrowserState, response: Any) -> None:
		"""Update message history with new interactions"""
		history_message = AgentMessagePrompt(state).get_message_for_history()
		self.messages.append(history_message)
		self.messages.append(AIMessage(content=response.model_dump_json(exclude_unset=True)))
		self.n_steps += 1

	def _log_response(self, response: Any) -> None:
		"""Log the model's response"""
		if 'Success' in response.current_state.valuation_previous_goal:
			emoji = '👍'
		elif 'Failed' in response.current_state.valuation_previous_goal:
			emoji = '⚠️'
		else:
			emoji = '🤷'

		logger.info(f'{emoji} Evaluation: {response.current_state.valuation_previous_goal}')
		logger.info(f'🧠 Memory: {response.current_state.memory}')
		logger.info(f'🎯 Next Goal: {response.current_state.next_goal}')
		logger.info(f'🛠️ Action: {response.action.model_dump_json(exclude_unset=True)}')

	def _save_conversation(self, input_messages: list[BaseMessage], response: Any) -> None:
		"""Save conversation history to file if path is specified"""
		if not self.save_conversation_path:
			return

		# create folders if not exists
		os.makedirs(os.path.dirname(self.save_conversation_path), exist_ok=True)

		with open(self.save_conversation_path + f'_{self.n_steps}.txt', 'w') as f:
			self._write_messages_to_file(f, input_messages)
			self._write_response_to_file(f, response)

	def _write_messages_to_file(self, f: Any, messages: list[BaseMessage]) -> None:
		"""Write messages to conversation file"""
		for message in messages:
			f.write(f' {message.__class__.__name__} \n')

			if isinstance(message.content, list):
				for item in message.content:
					if isinstance(item, dict) and item.get('type') == 'text':
						f.write(item['text'].strip() + '\n')
			elif isinstance(message.content, str):
				try:
					content = json.loads(message.content)
					f.write(json.dumps(content, indent=2) + '\n')
				except json.JSONDecodeError:
					f.write(message.content.strip() + '\n')

			f.write('\n')

	def _write_response_to_file(self, f: Any, response: Any) -> None:
		"""Write model response to conversation file"""
		f.write(' RESPONSE\n')
		f.write(json.dumps(json.loads(response.model_dump_json(exclude_unset=True)), indent=2))

	async def run(self, max_steps: int = 100) -> list[AgentHistory]:
		"""Execute the task with maximum number of steps"""
		try:
			logger.info(f'🚀 Starting task: {self.task}')

			for step in range(max_steps):
				if self._too_many_failures():
					break

				await self.step()

				if self._is_task_complete():
					logger.info('✅ Task completed successfully')
					break
			else:
				logger.info('❌ Failed to complete task in maximum steps')

			return self.history

		finally:
			if not self.controller_injected:
				self.controller.browser.close()

	def _too_many_failures(self) -> bool:
		"""Check if we should stop due to too many failures"""
		if self.consecutive_failures >= self.max_failures:
			logger.error(f'❌ Stopping due to {self.max_failures} consecutive failures')
			return True
		return False

	def _is_task_complete(self) -> bool:
		"""Check if the task has been completed successfully"""
		return bool(self.history and self.history[-1].result.is_done)
