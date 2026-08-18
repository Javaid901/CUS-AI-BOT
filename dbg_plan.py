import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend'))
from app.database import SessionLocal
from app.authority.service import authority_service
db = SessionLocal()
authority_service.refresh_cache(db)
db.close()

from app.orchestrator.planner import plan
from app.orchestrator.context import ConversationContext
from app.orchestrator.extractor import extract_entities
for raw in ['good morning', 'kya haal hai', 'meri complaint hai', 'result nahi aa raha',
            'who should I contact about my result', 'who handles examinations',
            'hi what is the mca fee', 'fees kitni hai bca ki', 'latest circular',
            'kya scheme Sabse acchi hai', 'show my bca semester 3 result']:
    ctx = ConversationContext()
    e = extract_entities(raw)
    p = plan(raw, ctx, 't1', e)
    print(f'{raw!r} -> {p.action} | {p.reason}')