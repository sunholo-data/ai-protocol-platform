"""Shared test doubles that model PRODUCTION semantics.

Why this package exists (v6.19.0, AIPLA #35 + #37): the default doubles are
more permissive than the real services, so a whole class of bug is invisible to
CI and only appears on a deployed environment — usually during a demo.

* ``InMemorySessionService`` lets ANY uid read ANY session. Real
  ``VertexAiSessionService`` enforces exact owner match.
* ADK state fixtures use a plain ``dict``, which has no concept of key-prefix
  scoping. Real ``State`` treats ``app:`` as application-global.

Both gaps have already produced production incidents — see the module
docstrings. Use these doubles for anything that touches session identity or
state scoping.
"""
