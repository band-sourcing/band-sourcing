import functools
import time
import logging

logger = logging.getLogger(__name__)


def retry_on_error(max_retries=3, backoff_base=1.0):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        logger.error(f"{func.__name__} 최종 실패: {e}")
                        raise

                    status = getattr(getattr(e, 'response', None), 'status_code', None)

                    if status == 429 or (status and status >= 500):
                        wait = backoff_base * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} 재시도 {attempt+1}/{max_retries} "
                            f"({wait}초 대기, status={status})"
                        )
                        time.sleep(wait)
                    elif hasattr(e, 'request'):
                        wait = backoff_base * (2 ** attempt)
                        logger.warning(
                            f"{func.__name__} 네트워크 에러, 재시도 {attempt+1}/{max_retries}"
                        )
                        time.sleep(wait)
                    else:
                        raise
        return wrapper
    return decorator
