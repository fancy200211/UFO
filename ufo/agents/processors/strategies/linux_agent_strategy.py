import json
from pathlib import Path
import traceback
from typing import List, TYPE_CHECKING


from ufo.agents.processors.app_agent_processor import AppAgentLoggingMiddleware
from ufo.agents.processors.context.processing_context import (
    ProcessingContext,
    ProcessingResult,
    ProcessingPhase,
)
from ufo.agents.processors.core.strategy_dependency import depends_on, provides
from ufo.agents.processors.schemas.actions import (
    ListActionCommandInfo,
    ActionCommandInfo,
)
from ufo.agents.processors.strategies.app_agent_processing_strategy import (
    AppActionExecutionStrategy,
    AppLLMInteractionStrategy,
)
from aip.messages import Command, Result, ResultStatus
from ufo import utils
from ufo.agents.processors.strategies.processing_strategy import BaseProcessingStrategy
from ufo.llm.response_schema import AppAgentResponse
from ufo.module.context import ContextNames

if TYPE_CHECKING:
    from ufo.agents.agent.customized_agent import LinuxAgent


@depends_on("log_path", "session_step")
@provides("clean_screenshot_path", "annotated_screenshot_path", "clean_screenshot_url", "annotated_screenshot_url", "desktop_observation")
class LinuxDesktopCollectionStrategy(BaseProcessingStrategy):
    """Collect and persist one observation before each Linux model call."""

    async def execute(self, agent, context):
        try:
            results = await context.global_context.command_dispatcher.execute_commands([
                Command(tool_name="get_desktop_observation", parameters={}, tool_type="data_collection")
            ])
            if not results or results[0].status != ResultStatus.SUCCESS:
                raise RuntimeError(results[0].error if results else "No desktop observation returned")
            observation = results[0].result
            if not isinstance(observation, dict) or not observation.get("screenshot_url"):
                raise ValueError("Desktop observation has no screenshot")
            directory = Path(context.get("log_path"))
            directory.mkdir(parents=True, exist_ok=True)
            prefix = directory / f"action_step{context.get('session_step', 0)}"
            clean_path, annotated_path = str(prefix) + ".png", str(prefix) + "_annotated.png"
            utils.save_image_string(observation["screenshot_url"], clean_path)
            utils.save_image_string(observation["annotated_screenshot_url"], annotated_path)
            metadata = {k: v for k, v in observation.items() if not k.endswith("screenshot_url")}
            Path(str(prefix) + "_ui.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            return ProcessingResult(success=True, phase=ProcessingPhase.DATA_COLLECTION, data={
                "clean_screenshot_path": clean_path, "annotated_screenshot_path": annotated_path,
                "clean_screenshot_url": observation["screenshot_url"],
                "annotated_screenshot_url": observation["annotated_screenshot_url"],
                "desktop_observation": metadata,
            })
        except Exception as exc:
            # CLI remains usable if observation fails; never reuse an old screenshot.
            self.logger.warning("Linux desktop observation failed: %s", exc)
            return ProcessingResult(success=True, phase=ProcessingPhase.DATA_COLLECTION, data={
                "clean_screenshot_path": "", "annotated_screenshot_path": "",
                "clean_screenshot_url": None, "annotated_screenshot_url": None,
                "desktop_observation": {"ui_tree": [], "controls": [], "warnings": [str(exc)]},
            })


@depends_on("request")
@provides(
    "parsed_response",
    "response_text",
    "llm_cost",
    "prompt_message",
    "action",
    "thought",
    "comment",
)
class LinuxLLMInteractionStrategy(AppLLMInteractionStrategy):
    """
    Strategy for LLM interaction with Linux Agent specific prompting.

    This strategy handles:
    - Context-aware prompt construction with app-specific data
    - Control information integration in prompts
    - LLM interaction with retry logic
    - Response parsing and validation
    """

    def __init__(self, fail_fast: bool = True) -> None:
        """
        Initialize App Agent LLM interaction strategy.
        :param fail_fast: Whether to raise exceptions immediately on errors
        """
        super().__init__(fail_fast=fail_fast)

    async def execute(
        self, agent: "LinuxAgent", context: ProcessingContext
    ) -> ProcessingResult:
        """
        Execute LLM interaction for Linux Agent.
        :param agent: The LinuxAgent instance
        :param context: Processing context with control and screenshot data
        :return: ProcessingResult with parsed response and cost
        """
        try:
            request = context.get("request")
            plan = self._get_prev_plan(agent)

            # Build comprehensive prompt
            self.logger.info("Building Linux Agent prompt")
            # Get blackboard context
            blackboard_prompt = []
            if not agent.blackboard.is_empty():
                blackboard_prompt = agent.blackboard.blackboard_to_prompt()

            prompt_message = agent.message_constructor(
                dynamic_examples=[],
                dynamic_knowledge="",
                plan=plan,
                request=request,
                blackboard_prompt=blackboard_prompt,
                last_success_actions=self._get_last_success_actions(agent=agent),
                screenshot_url=context.get_local("clean_screenshot_url"),
                annotated_screenshot_url=context.get_local("annotated_screenshot_url"),
                desktop_observation=context.get_local("desktop_observation", {}),
            )

            # Get LLM response
            self.logger.info("Getting LLM response for Linux Agent")
            response_text, llm_cost = await self._get_llm_response(
                agent, prompt_message
            )

            # Parse and validate response
            self.logger.info("Parsing Linux Agent response")
            parsed_response = self._parse_app_response(agent, response_text)

            # Extract structured data
            structured_data = parsed_response.model_dump()

            return ProcessingResult(
                success=True,
                data={
                    "parsed_response": parsed_response,
                    "response_text": response_text,
                    "llm_cost": llm_cost,
                    "prompt_message": prompt_message,
                    **structured_data,
                },
                phase=ProcessingPhase.LLM_INTERACTION,
            )

        except Exception as e:
            error_msg = f"App LLM interaction failed: {str(e)}"
            self.logger.error(error_msg)
            return self.handle_error(e, ProcessingPhase.LLM_INTERACTION, context)


class LinuxActionExecutionStrategy(AppActionExecutionStrategy):
    """
    Strategy for executing actions in Linux Agent.

    This strategy handles:
    - Action execution based on parsed LLM response
    - Result capturing and error handling
    """

    def __init__(self, fail_fast: bool = True) -> None:
        """
        Initialize Linux action execution strategy.
        :param fail_fast: Whether to raise exceptions immediately on errors
        """
        super().__init__(fail_fast=fail_fast)

    async def execute(
        self, agent: "LinuxAgent", context: ProcessingContext
    ) -> ProcessingResult:
        """
        Execute Linux Agent actions.
        :param agent: The AppAgent instance
        :param context: Processing context with response and control data
        :return: ProcessingResult with execution results
        """
        try:
            # Step 1: Extract context variables
            parsed_response: AppAgentResponse = context.get_local("parsed_response")
            command_dispatcher = context.global_context.command_dispatcher

            if not parsed_response:
                return ProcessingResult(
                    success=True,
                    data={"message": "No response available for action execution"},
                    phase=ProcessingPhase.ACTION_EXECUTION,
                )

            # Execute the action
            execution_results = await self._execute_app_action(
                command_dispatcher, parsed_response.action
            )

            # Create action info for memory
            actions = self._create_action_info(
                parsed_response.action,
                execution_results,
            )

            # Print action info
            action_info = ListActionCommandInfo(actions)
            action_info.color_print()

            # Create control log
            control_log = action_info.get_target_info()

            status = (
                parsed_response.action.status
                if isinstance(parsed_response.action, ActionCommandInfo)
                else action_info.status
            )
            if status != "FAIL" and any(a.result.status != ResultStatus.SUCCESS for a in actions):
                status = "CONTINUE"

            return ProcessingResult(
                success=True,
                data={
                    "execution_result": execution_results,
                    "action_info": action_info,
                    "control_log": control_log,
                    "status": status,
                },
                phase=ProcessingPhase.ACTION_EXECUTION,
            )

        except Exception as e:

            error_msg = f"App action execution failed: {str(traceback.format_exc())}"
            self.logger.error(error_msg)
            return self.handle_error(e, ProcessingPhase.ACTION_EXECUTION, context)

    def _create_action_info(
        self,
        actions: ActionCommandInfo | List[ActionCommandInfo],
        execution_results: List[Result],
    ) -> List[ActionCommandInfo]:
        """
        Create action information for memory tracking.
        :param control_info: List of filtered controls
        :param response: Parsed response
        :param execution_result: Execution results
        :return: ActionCommandInfo object
        """
        actions = [actions] if isinstance(actions, ActionCommandInfo) else (actions or [])
        results = iter(execution_results or [])
        for action in actions:
            if not action.function:
                action.result = Result(status=ResultStatus.SUCCESS, result="No action taken.")
                continue
            result = next(results, None)
            if result is None:
                result = Result(status=ResultStatus.FAILURE, error="Missing execution result")
            payload = result.result
            if isinstance(payload, dict) and (payload.get("success") is False or payload.get("exit_code", 0) not in (0, None)):
                result.status = ResultStatus.FAILURE
                result.error = str(payload.get("error") or payload.get("stderr") or f"Command exited with {payload.get('exit_code')}")
            action.result = result
        return actions


class LinuxLoggingMiddleware(AppAgentLoggingMiddleware):
    """
    Specialized logging middleware for Linux Agent with enhanced contextual information.
    """

    async def on_error(self, processor, error):
        await super().on_error(processor, error)
        # The shared logger does not set a status. An empty Linux status maps to
        # a terminal None state, which otherwise looks like successful completion.
        context = processor.processing_context
        context.set_local("status", "FAIL")
        context.set_local("result", str(error))
        context.global_context.set(ContextNames.ROUND_RESULT, str(error))

    def starting_message(self, context: ProcessingContext) -> str:
        """
        Return the starting message of the agent.
        :param context: Processing context with round and step information
        :return: Starting message string
        """

        # Try both global and local context for request
        request = (
            context.get("request") or context.get_local("request") or "Unknown Request"
        )

        return (
            f"Completing the user request: [bold cyan]{request}[/bold cyan] on Linux."
        )
