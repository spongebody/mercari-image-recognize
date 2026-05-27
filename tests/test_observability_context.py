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
