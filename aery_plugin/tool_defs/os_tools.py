"""OS-level tools for the Aery QGIS agent.

Historically this module exposed a raw ``bash`` tool (``subprocess.Popen``
with ``shell=True``) and an ``install_package`` tool built on a raw
``subprocess`` call. Both were removed for security: a ``shell=True``
bash tool is a remote-code-execution footgun, and arbitrary
``subprocess`` calls escape the run_qgis_code sandbox. Package
installation is handled by the core ``pip_install`` tool
(``tools.py`` -> ``_execute_pip_install``), which runs a scoped
``pip install`` and is itself gated by the permission system.

This module is intentionally empty now so the loader
(``_register_external_tools``) has nothing unsafe to register.
Keeping the file (vs. deleting it) avoids churn in the
``tool_modules`` list and any tests that import it.
"""

TOOLS: list[dict] = []
