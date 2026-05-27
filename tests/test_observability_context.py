from concurrent.futures import ThreadPoolExecutor

from app.observability import context as ctx


def test_set_get_reset_request_id():
    assert ctx.get_request_id() is None
    token = ctx.set_request_id("rid-1")
    try:
        assert ctx.get_request_id() == "rid-1"
    finally:
        ctx.reset_request_id(token)
    assert ctx.get_request_id() is None


def test_propagate_into_worker_thread():
    """Workers receive request_id as a parameter and set it via the contextvar API.

    Python ContextVars do NOT auto-propagate to threads — the value must be carried
    explicitly. This test verifies the API works correctly inside a worker thread
    and that the parent thread's contextvar is unaffected. The Task 11 wrapper
    relies on this pattern to thread request_id through ThreadPoolExecutor.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        def worker(request_id: str):
            token = ctx.set_request_id(request_id)
            try:
                return ctx.get_request_id()
            finally:
                ctx.reset_request_id(token)

        fut = pool.submit(worker, "rid-bg")
        assert fut.result() == "rid-bg"
        # parent thread untouched
        assert ctx.get_request_id() is None
    finally:
        pool.shutdown()
