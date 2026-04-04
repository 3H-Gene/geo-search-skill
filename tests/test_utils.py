"""Tests for utils module."""
import pytest
import time
from sra_search.utils.rate_limiter import RateLimiter
from sra_search.utils.retry import with_retry, RetryError


class TestRateLimiter:
    """Test cases for RateLimiter."""

    def test_acquire_token_sync(self):
        """Test acquiring a token synchronously (should be immediate)."""
        limiter = RateLimiter(rate=10.0)
        start = time.time()
        limiter.acquire()
        elapsed = time.time() - start
        assert elapsed < 0.5  # Should be fast for first token

    @pytest.mark.asyncio
    async def test_acquire_async_immediate(self):
        """Test async token acquire when bucket is full."""
        limiter = RateLimiter(rate=10.0)
        start = time.time()
        await limiter.acquire_async()
        elapsed = time.time() - start
        assert elapsed < 0.5

    def test_rate_limiter_init(self):
        """Test rate limiter initializes correctly."""
        limiter = RateLimiter(rate=5.0)
        assert limiter.rate == 5.0
        assert limiter.capacity == 5.0
        assert limiter.tokens == 5.0

    def test_report_success_resets_429(self):
        """Test that report_success resets consecutive_429 counter."""
        limiter = RateLimiter(rate=3.0)
        limiter.consecutive_429 = 2
        limiter.report_success()
        assert limiter.consecutive_429 == 0

    def test_report_429_increments_counter(self):
        """Test that report_429 increments the counter."""
        limiter = RateLimiter(rate=3.0)
        assert limiter.consecutive_429 == 0
        limiter.report_429()
        assert limiter.consecutive_429 == 1

    def test_report_429_pauses_after_3(self):
        """Test that 3 consecutive 429s triggers a pause."""
        limiter = RateLimiter(rate=3.0)
        limiter.report_429()
        limiter.report_429()
        result = limiter.report_429()
        assert result is True
        assert limiter.paused_until is not None


class TestRetry:
    """Test cases for retry mechanism (with_retry decorator)."""

    def test_retry_success_on_second_attempt(self):
        """Test retry with eventual success."""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01)
        def eventually_success():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Not yet")
            return "success"

        result = eventually_success()
        assert result == "success"
        assert call_count == 2

    def test_retry_exhausted_raises_retry_error(self):
        """Test retry with all attempts failed raises RetryError."""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01)
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        with pytest.raises(RetryError):
            always_fail()

        assert call_count == 3  # max_attempts = 3

    def test_no_retry_on_success(self):
        """Test no retry needed on first success."""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01)
        def immediate_success():
            nonlocal call_count
            call_count += 1
            return "success"

        result = immediate_success()
        assert result == "success"
        assert call_count == 1

    def test_retry_with_specific_exception(self):
        """Test retry only on specific exception type."""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01, retryable_exceptions=(ValueError,))
        def fail_on_value_error():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Retry this")
            raise TypeError("Don't retry this")

        with pytest.raises(TypeError):
            fail_on_value_error()

        assert call_count == 2

    def test_retry_returns_correct_value(self):
        """Test that retry returns correct value."""
        @with_retry(max_attempts=2, base_delay=0.01)
        def return_42():
            return 42

        assert return_42() == 42

    def test_calculate_delay_range(self):
        """Test that delay calculation is within expected range."""
        from sra_search.utils.retry import calculate_delay
        delay = calculate_delay(0, base_delay=2.0, max_delay=60.0, jitter=0.3)
        assert 0.1 <= delay <= 60.0


class TestCalculateDelay:
    """Standalone tests for the calculate_delay function."""

    def test_calculate_delay_positive(self):
        """Test delay calculation with positive attempt."""
        from sra_search.utils.retry import calculate_delay
        delay = calculate_delay(attempt=2, base_delay=1.0, max_delay=30.0, jitter=0.1)
        assert delay > 0

    def test_calculate_delay_max_cap(self):
        """Test delay is capped at max_delay."""
        from sra_search.utils.retry import calculate_delay
        delay = calculate_delay(attempt=100, base_delay=1.0, max_delay=10.0, jitter=0.0)
        assert delay <= 10.0
