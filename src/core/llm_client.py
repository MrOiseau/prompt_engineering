import logging
import time
from typing import Any, Optional

from openai import APIError, APITimeoutError, OpenAI, RateLimitError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.core.config import settings
from src.core.log_setup import setup_logging

logger = setup_logging(__name__)

class LLMClient:
    """
    A robust LLM client handling retries, timeouts, and monitoring.
    Uses OpenAI's Python client with Tenacity, configured via the settings module.
    """
    def __init__(self, model: str):
        """
        Initialize the LLM Client.

        Args:
            model: Model name to use (required).
        """
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = model
        self.total_tokens = 0

    @retry(
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def call(
        self, 
        messages: list[dict[str, str]], 
        temperature: float = 0.0,
        response_format: Optional[dict[str, Any]] = None,
        timeout: float = 60.0,
        reasoning_effort: Optional[str] = None
    ) -> str:
        """
        Executes a chat completion call with retries and timeout handling.

        Args:
            messages: List of message dictionaries.
            temperature: Sampling temperature (default 0.0). Note: Some reasoning models require 1.0.
            response_format: Optional JSON schema or configuration.
            timeout: Request timeout in seconds.
            reasoning_effort: Effort level for reasoning models (e.g., "low", "medium", "high").

        Returns:
            str: The content string of the model's response.
        """
        start_time = time.time()
        
        try:
            # Prepare arguments
            request_kwargs = {
                "model": self.model,
                "messages": messages,
                "response_format": response_format,
                "timeout": timeout
            }
            
            # Application of Optional Parameters
            if reasoning_effort:
                request_kwargs["reasoning_effort"] = reasoning_effort
            
            # Handle Temperature
            # Some reasoning models (e.g. o1, gpt-5-nano) do not support temperature != 1.0
            # or explicit temperature control combined with reasoning_effort.
            # We trust the caller/config to provide the right value, 
            # OR we strictly omit it if reasoning_effort is present/model is known.
            # Given user feedback "model cannot accept a temperature other than a default of 1",
            # we should be careful.
            
            # Heuristic: If reasoning_effort is used, skip temperature (often mutually exclusive or fixed).
            # Or if model is explicitly one of the reasoning ones (handled via config usually).
            if not reasoning_effort:
                request_kwargs["temperature"] = temperature
            elif temperature != 1.0 and temperature != 0.0: 
                # If specific non-default temp passed with reasoning, warn or permit?
                # User said "cannot accept temp other than default of 1".
                # Safest is to OMIT temperature if reasoning_effort is set, relying on model default.
                logger.debug("Omitting temperature due to presence of reasoning_effort")
                pass
            
            logger.debug(f"Sending request to {self.model} (kwargs={request_kwargs})")
            
            response = self.client.chat.completions.create(**request_kwargs)
            
            # Track usage
            if response.usage:
                self.total_tokens += response.usage.total_tokens
                logger.info(
                    f"Token usage: {response.usage.total_tokens} (Total: {self.total_tokens})"
                )

            content = response.choices[0].message.content
            duration = time.time() - start_time
            logger.debug(f"Request completed in {duration:.2f}s")
            
            if content is None:
                logger.warning("LLM returned None content")
                return ""
                
            return content

        except Exception as e:
            logger.error(f"LLM call failed after retries: {str(e)}")
            raise
