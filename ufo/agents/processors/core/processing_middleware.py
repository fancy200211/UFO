import logging
import traceback
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from flask import json

from ufo.agents.processors.context.processing_context import (
    ProcessingContext,
    ProcessingResult,
)
from ufo.agents.processors.core.processor_framework import (
    ProcessingContext,
    ProcessingResult,
)

from ufo.module.context import ContextNames
from pydantic_core import to_jsonable_python

if TYPE_CHECKING:
    from ufo.agents.processors.core.processor_framework import ProcessorTemplate
    from ufo.module.basic import FileWriter


class ProcessorMiddleware(ABC):
    """
    Processor middleware base class.
    """

    def __init__(self, name: Optional[str] = None):
        """
        Initialize the middleware.
        :param name: Optional custom name for the middleware. If not provided, uses class name.
        """
        self.name = name or self.__class__.__name__

    @abstractmethod
    async def before_process(
        self, processor: "ProcessorTemplate", context: ProcessingContext
    ) -> None:
        """
        Before processing hook.
        :param processor: The processor instance.
        :param context: The processing context.
        """
        pass

    @abstractmethod
    async def after_process(
        self, processor: "ProcessorTemplate", result: ProcessingResult
    ) -> None:
        """
        After processing hook.
        :param processor: The processor instance.
        :param result: The processing result.
        """
        pass

    @abstractmethod
    async def on_error(self, processor: "ProcessorTemplate", error: Exception) -> None:
        """
        Error handling hook.
        :param processor: The processor instance.
        :param error: The error that occurred.
        """
        pass


class EnhancedLoggingMiddleware(ProcessorMiddleware):
    """
    Enhanced logging middleware that handles different types of errors appropriately.
    """

    def __init__(self, log_level: int = logging.INFO, name: Optional[str] = None):
        super().__init__(name)
        self.logger = logging.getLogger(f"{self.__class__.__name__}.{self.name}")
        self.log_level = log_level

    async def before_process(
        self, processor: "ProcessorTemplate", context: ProcessingContext
    ) -> None:
        """Log processing start with context information."""
        round_num = context.get("round_num", 0)
        round_step = context.get("round_step", 0)

        self.logger.log(
            self.log_level,
            f"Starting processing: Round {round_num + 1}, Step {round_step + 1}, "
            f"Processor: {processor.__class__.__name__}",
        )

    async def after_process(
        self, processor: "ProcessorTemplate", result: ProcessingResult
    ) -> None:
        """Log processing completion with result summary."""
        if result.success:
            self.logger.log(
                self.log_level,
                f"Processing completed successfully in {result.execution_time:.2f}s",
            )

            # Log phase execution times if available
            data_keys = list(result.data.keys())
            if data_keys:
                self.logger.debug(f"Result data keys: {data_keys}")
        else:
            self.logger.warning(f"Processing completed with failure: {result.error}")

        local_logger: "FileWriter" = processor.processing_context.global_context.get(
            ContextNames.LOGGER
        )
        local_context = processor.processing_context.local_context

        local_context.total_time = result.execution_time

        phrase_time_cost = {}
        for phrase, phrase_result in processor.processing_context.phase_results.items():
            phrase_time_cost[phrase.name] = phrase_result.execution_time

        local_context.execution_times = phrase_time_cost

        # Persist the structured latency record next to the trace files.
        self._persist_latency(processor, local_context)

        if local_logger is None:
            # Some deployments (e.g. headless Galaxy workers) do not attach a
            # file writer; the trace line is simply skipped in that case.
            self.logger.debug("No local logger configured; response trace not written.")
            return

        safe_obj = to_jsonable_python(local_context.to_dict(selective=True))

        local_context_string = json.dumps(safe_obj, ensure_ascii=False)

        local_logger.write(local_context_string)

        self.logger.info("Log saved successfully.")

    def _persist_latency(self, processor: "ProcessorTemplate", local_context) -> None:
        """
        Append the step latency record to ``latency.steps.jsonl`` and refresh
        the ``latency.json`` / ``latency.md`` rollup.

        Telemetry failures are swallowed on purpose.

        :param processor: The processor that just finished a step.
        :param local_context: Local context holding the ``latency`` record.
        """
        try:
            record = getattr(local_context, "latency", None)
            if not record:
                return

            log_path = processor.processing_context.get_local("log_path")
            if not log_path:
                log_path = processor.processing_context.global_context.get(
                    ContextNames.LOG_PATH
                )
            if not log_path:
                return

            from ufo.utils.latency import LatencyStore

            LatencyStore(str(log_path)).append_step(record)
        except Exception as error:  # pragma: no cover - telemetry must not break a step
            self.logger.debug(f"Failed to persist latency record: {error}")

    async def on_error(self, processor: "ProcessorTemplate", error: Exception) -> None:
        """Enhanced error logging with context information."""

        from ufo.agents.processors.core.processor_framework import ProcessingException

        if isinstance(error, ProcessingException):
            # record error
            self.logger.error(
                f"ProcessingException in {processor.__class__.__name__}:\n"
                f"  Phase: {error.phase}\n"
                f"  Message: {str(error)}\n"
                f"  Context: {error.context_data}\n"
                f"  Original Exception: {error.original_exception}"
            )

            if error.original_exception:
                self.logger.info(
                    f"Original traceback:\n{''.join(traceback.format_exception(type(error.original_exception), error.original_exception, error.original_exception.__traceback__))}"
                )
        else:
            # 记录其他类型的异常
            self.logger.error(
                f"Unexpected error in {processor.__class__.__name__}: {str(error)}\n"
                f"Error type: {type(error).__name__}\n"
                f"Traceback:\n{''.join(traceback.format_exception(type(error), error, error.__traceback__))}"
            )
