import os
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

csrf = CSRFProtect()

redis_url = os.environ.get("REDIS_URL", "")
if redis_url:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=redis_url,
        strategy="fixed-window"
    )
else:
    limiter = Limiter(
        key_func=get_remote_address,
        strategy="fixed-window"
    )
