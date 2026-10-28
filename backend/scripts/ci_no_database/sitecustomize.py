"""Install requested database instrumentation before Python child imports."""

import os

if os.getenv("CI_NO_DATABASE_ATTEMPTS") or os.getenv("CI_DATABASE_OBSERVATIONS"):
    from scripts.database_child_startup import install

    install()
