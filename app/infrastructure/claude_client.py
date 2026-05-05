import anthropic
import structlog
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type
)

logger = structlog.get_logger(__name__)


class ClaudeClient:
    """Async wrapper around the Anthropic SDK with retry logic."""

    def __init__(self, api_key: str, model: str, max_tokens: int = 4096,
                 temperature: float = 0.0, max_retries: int = 3):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_retries = max_retries

    async def complete(self, system_prompt: str, user_message: str,
                       max_tokens: int | None = None,
                       temperature: float | None = None) -> str:
        """Send a message to Claude and return the text response."""
        return await self._complete_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=max_tokens or self._max_tokens,
            temperature=temperature if temperature is not None else self._temperature,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
        )),
        before_sleep=lambda retry_state: structlog.get_logger().warning(
            "claude_api_retry",
            attempt=retry_state.attempt_number,
            wait=retry_state.next_action.sleep,
        ),
    )
    async def _complete_with_retry(self, system_prompt: str, user_message: str,
                                   max_tokens: int, temperature: float) -> str:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        except anthropic.BadRequestError as e:
            error_msg = str(e)
            if "credit balance" in error_msg.lower():
                logger.error("anthropic_no_credits", error=error_msg)
                raise anthropic.BadRequestError(
                    message="Anthropic API credit balance is too low. "
                            "Please add credits at https://console.anthropic.com/settings/billing",
                    response=e.response,
                    body=e.body,
                )
            raise
        except anthropic.AuthenticationError as e:
            logger.error("anthropic_auth_failed", error=str(e))
            raise

    async def close(self) -> None:
        await self._client.close()
