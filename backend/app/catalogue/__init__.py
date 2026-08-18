"""
backend/app/catalogue/__init__.py

NEP Academic Catalogue module.

A structured knowledge source for programme / curriculum exploration that
runs alongside the existing RAG pipeline. The AI Orchestrator routes
academic catalogue queries here and only falls back to generic document
retrieval when the catalogue has no matching structured data.

Submodules:
  models.py     — ORM models (programmes, subjects, minors, outcomes, docs)
  service.py    — query + admin CRUD layer
  detect.py     — rule-based catalogue request detection for the planner
  responses.py  — chat response builders for the orchestrator
  backend.py    — orchestrator handlers (pickers + continuity)
  seed.py       — demo-mode seed data
  routes.py     — admin API (CRUD + curriculum PDF upload)
"""