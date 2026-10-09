from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

### Strict tier: routes that cost money, send email, write data, or are abuse targets.
STRICT_REGISTER = "5/hour"
STRICT_USERNAME_CHECK = "30/minute"
STRICT_FORM_SUBMISSION = "10/hour"
STRICT_PAYMENT = "10/minute"

### Moderate tier: authenticated actions and reads that expose individual records.
MODERATE = "60/minute"

### Loose tier: public reads. Applied by default to every route without its own limit.
LOOSE = "200/minute"

def user_or_ip(request: Request) -> str:
    """
    Key function to identify who a request belongs to for rate limiting.

    ::param request the Request object containing the client information

    ::return the user id when the request was authenticated, otherwise the client ip address
    """

    user_id = getattr(request.state, "user_id", None)

    if user_id:
        return f"user:{user_id}"

    return f"ip:{get_remote_address(request)}"

limiter = Limiter(
    key_func = user_or_ip,
    default_limits = [LOOSE],
    strategy = "moving-window",
    key_style = "endpoint",
    headers_enabled = True,
    storage_uri = "memory://"
)

def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Exception handler returning a 429 in the same detail format as the rest of the API.

    ::param request the Request object that exceeded its rate limit
    ::param exc the RateLimitExceeded exception raised by the limiter

    ::return a 429 JSON response including the Retry-After and X-RateLimit headers
    """

    response = JSONResponse(
        status_code = 429,
        content = {"detail": "Too many requests. Please try again later."}
    )

    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
