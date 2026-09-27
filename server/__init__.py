"""Luau deobfuscator web service.

Modules
-------
``app``        FastAPI routes and the static front end
``jobs``       job store, worker pool, SSE event log
``engines``    which pipeline runs for which obfuscator, in its own process group
``detectors``  fingerprints for every family in the sample corpus
``branding``   the site's own output header, bound at runtime
``runtime``    native binary provisioning (luau, luau-ast, lune) and health
``worker_*``   subprocess drivers for each vendored engine
"""
__version__ = "1.0.0"
