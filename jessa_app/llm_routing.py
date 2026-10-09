"""Conservative routing and request-local telemetry; never log candidate text."""
import re
from contextvars import ContextVar
from . import config
REQUEST_TRACE: ContextVar[list | None] = ContextVar('jessa_llm_trace', default=None)

def reset_trace(): REQUEST_TRACE.set([])
def record_trace(**values):
    current=REQUEST_TRACE.get()
    if current is not None: current.append(values)
def get_trace(): return list(REQUEST_TRACE.get() or [])

# Whole-question matches plus a matching documented canonical field are required.
FACT_QUESTIONS = (
    (r'(?:what is your )?(?:legal |full )?name\??', r'(?im)^- Legal name:\s*\S'),
    (r'(?:what is your )?(?:preferred name)\??', r'(?im)^- Preferred name:\s*\S'),
    (r'(?:what is your )?(?:email(?: address)?)\??', r'(?im)^- W2/job-search email:\s*\S'),
    (r'(?:what is your )?(?:phone(?: number)?)\??', r'(?im)^- Phone:\s*\S'),
    (r'(?:what is your )?(?:linkedin(?: profile)?(?: url)?)\??', r'(?im)^- LinkedIn:\s*https://'),
)
def supplemental_tier(questions: str, canonical: str) -> str:
    lines=[line.strip().lower() for line in questions.splitlines() if line.strip()]
    if not lines: return 'intensive'
    for line in lines:
        if not any(re.fullmatch(question,line) and re.search(fact,canonical) for question,fact in FACT_QUESTIONS):
            return 'intensive'
    return 'small'

def openai_route(tier: str) -> tuple[str,str]:
    if tier == 'small': return config.OPENAI_SMALL_MODEL, config.OPENAI_SMALL_REASONING
    return config.OPENAI_INTENSIVE_MODEL, config.OPENAI_INTENSIVE_REASONING
