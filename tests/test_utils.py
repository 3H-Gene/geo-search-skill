"""Tests for utils module."""
import pytest
import asyncio
import time
from sra_search.utils.rate_limiter import RateLimiter
from sra_search.utils.retry import with_retry


class TestRateLimiter:
    """Test cases for RateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire_token(self):
        """Test acquiring a token."""
        limiter = RateLimiter(rate=10, per=1.0)  # 10 per second
        
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start
        
        assert elapsed < 0.1  # Should be fast

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test rate limiting behavior."""
        limiter = RateLimiter(rate=2, per=1.0)  # 2 per second
        
        start = time.time()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.time() - start
        
        # Should take about 1 second for 2 tokens at rate 2/sec
        assert elapsed >= 0.9

    @pytest.mark.asyncio
    async def test_burst_requests(self):
        """Test handling burst requests."""
        limiter = RateLimiter(rate=5, per=1.0)
        
        # Try to acquire more than rate
        start = time.time()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.time() - start
        
        # Should complete quickly for burst within limit
        assert elapsed < 0.5


class TestRetry:
    """Test cases for retry mechanism."""

    def test_retry_success(self):
        """Test retry with eventual success."""
        call_count = 0
        
        @with_retry(max_retries=3, base_delay=0.01)
        def eventually_success():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Not yet")
            return "success"
        
        result = eventually_success()
        assert result == "success"
        assert call_count == 2

    def test_retry_exhausted(self):
        """Test retry with all attempts failed."""
        call_count = 0
        
        @with_retry(max_retries=3, base_delay=0.01)
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")
        
        with pytest.raises(ValueError):
            always_fail()
        
        assert call_count == 4  # initial + 3 retries

    def test_no_retry_on_success(self):
        """Test no retry needed on first success."""
        call_count = 0
        
        @with_retry(max_retries=3, base_delay=0.01)
        def immediate_success():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = immediate_success()
        assert result == "success"
        assert call_count == 1

    def test_retry_with_specific_exception(self):
        """Test retry only on specific exception."""
        call_count = 0
        
        @with_retry(max_retries=3, base_delay=0.01, exceptions=(ValueError,))
        def fail_on_value_error():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Retry this")
            raise TypeError("Don't retry this")
        
        with pytest.raises(TypeError):
            fail_on_value_error()
        
        assert call_count == 2
