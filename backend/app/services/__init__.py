"""
backend/app/services/__init__.py

Student Service Connectors — modular, replaceable adapters for university services.

Each connector implements the ServiceConnector interface defined in base.py.
Connectors are registered in registry.py and discovered by the Orchestrator.

Architecture:
  Orchestrator → Registry → Connector (authenticate + fetch)

To integrate a real university portal:
  1. Subclass ServiceConnector
  2. Implement authenticate() and fetch()
  3. Register in registry.py
  4. Done — no chatbot code changes needed
"""
