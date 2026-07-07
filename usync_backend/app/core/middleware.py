import time

from fastapi import Request

async def add_process_time(request: Request, call_next):
    """
    Middleware wrapper to run prior to and after all functions within the backend.

    ::param request the fastapi Request
    ::param call_next the function to call
    """

    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time"] = str(time.perf_counter() - start)
    return response
