"""Install requested database instrumentation before Python child imports."""

import os

if os.getenv("CI_NO_DATABASE_ATTEMPTS"):
    from scripts.ci_no_database import install

    install()
if os.getenv("CI_DATABASE_OBSERVATIONS"):
    from scripts.database_observation import install as observe

    observe()
