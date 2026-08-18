from app.services.university_connectors import AdmitCardConnector
import inspect

# Check if fetch is defined directly on the class
print('fetch in AdmitCardConnector.__dict__:', 'fetch' in AdmitCardConnector.__dict__)

# Check MRO
for cls in AdmitCardConnector.__mro__:
    has_fetch = 'fetch' in cls.__dict__
    print(f'  {cls.__name__}.__dict__ has fetch: {has_fetch}')

# Check if fetch comes from ServiceConnector base
from app.services.base import ServiceConnector
print()
print('ServiceConnector.fetch defined?:', 'fetch' in ServiceConnector.__dict__)

# Check if AdmitCardConnector has fetch as a method (even if not in __dict__)
print()
print('AdmitCardConnector.fetch callable?:', hasattr(AdmitCardConnector, 'fetch'))

# Try inspecting
try:
    members = inspect.getmembers(AdmitCardConnector, predicate=lambda x: inspect.isfunction(x) or inspect.ismethod(x))
    fetch_members = [name for name, _ in members if 'fetch' in name.lower()]
    print('Fetch-related members:', fetch_members)
except Exception as e:
    print('Error:', e)