import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend'))
from app.database import SessionLocal
from app.authority.service import authority_service

db = SessionLocal()
authority_service.refresh_cache(db)
print('active in cache:', len(authority_service.list_active()))
db.close()

from app.orchestrator.planner import _detect_authority_intent
for t in ['who should I contact about my result', 'who is the registrar',
          'who handles examinations', 'contact the controller of examinations',
          'who is the dean of science']:
    m = _detect_authority_intent(t)
    print(repr(t), '->', len(m), 'matches:', [x.get('authority_name') for x in m])