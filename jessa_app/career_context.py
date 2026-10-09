"""Protect canonical controls and a complete baseline from retrieval truncation."""
from __future__ import annotations
import re
from typing import Literal
from .evidence import build_evidence_context, visible_evidence
BaselineId = Literal['practical', 'executive', 'technical']
BASELINES = ('practical', 'executive', 'technical')

def select_baseline(title: str, override: str | None = None) -> str:
    if override is not None:
        if override not in BASELINES: raise ValueError('Unknown resume baseline')
        return override
    title = title.lower()
    if not title.strip(): return 'practical'
    if re.search(r'\b(vp|vice president|cio|chief information officer|chief technology officer|cto|head of (it|technology|information technology))\b', title):
        return 'executive'
    if re.search(r'\b(director|manager|head of)\b', title): return 'practical'
    return 'technical'

class CareerContext(str):
    def __new__(cls, core: str, controls: str, baseline: dict, evidence: str, linkedin: str):
        payload = dict(canonical_profile=core, mandatory_claim_controls=controls,
                       selected_resume_baseline=baseline, supporting_evidence=evidence,
                       secondary_cached_linkedin=linkedin)
        text = '\n\n'.join([core, controls, baseline.get('content',''), evidence, linkedin])
        obj = super().__new__(cls, text)
        obj.payload = payload
        obj.provenance = {k:v for k,v in baseline.items() if k != 'content'}
        return obj

def assemble_context(core: str, rows: list[dict], job: dict, linkedin: str = '', override: str | None = None) -> CareerContext:
    rows = visible_evidence(rows, job.get('id'))
    baseline_id = select_baseline(str(job.get('title') or ''), override)
    selected = [r for r in rows if r.get('scope') == 'global' and r.get('category') == 'resume-baseline-' + baseline_id]
    controls = [r for r in rows if r.get('category') == 'claim-controls' or r.get('claim_status') == 'do_not_claim']
    controls.sort(key=lambda r: (str(r.get('source_path','')), int(r.get('source_line_start') or 0)))
    baseline = {'id': baseline_id, 'source_sha256': '', 'content': ''}
    if selected:
        # The sync contract supplies one full-text chunk per active baseline.
        if len(selected) != 1: raise ValueError('Baseline must contain exactly one complete source chunk')
        item=selected[0]
        baseline.update(source_sha256=item.get('source_sha256',''), content=item['content'], source_path=item.get('source_path',''), version=next((t.split(':',1)[1] for t in item.get('tags',[]) if t.startswith('baseline-version:')), 'unversioned'))
    optional=[r for r in rows if r not in controls and r.get('artifact_type') != 'resume_base']
    evidence=build_evidence_context(optional, '\n'.join(str(job.get(k) or '') for k in ('title','description','company')))
    return CareerContext(core, '\n\n'.join(str(r.get('content') or '') for r in controls), baseline, evidence, linkedin[:3500])

def context_payload(profile: str) -> dict:
    if isinstance(profile, CareerContext): return profile.payload
    return {'canonical_profile': profile}
