"""Synthetic fixture generators for the reflib per-type benchmark corpus.

ADR-028 measurement prereq. Each `generate_<type>_fixtures.py` script is
idempotent (overwrite-safe) and seed-controlled (deterministic content).

Generated fixtures land under
``reference-library/per-type-fixtures/<type>/`` and are committed alongside
the scripts. The scripts run once to populate, then live as the regen path
when fixture shape changes.

Naming convention: every fixture uses generic agent / project names
(``agent-alpha``, ``our-team``, ``project-falcon``) per F32 — never real
people, clients, or organisations.
"""
