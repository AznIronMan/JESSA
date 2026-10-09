import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from jessa_app.career_context import assemble_context, select_baseline, context_payload
from jessa_app.llm_routing import supplemental_tier, reset_trace, get_trace
from jessa_app.services import llm

class CareerAlignmentTests(unittest.TestCase):
    def rows(self):
        return [dict(scope='global',category='resume-baseline-'+b,artifact_type='resume_base',content=b+' COMPLETE END',source_sha256='hash-'+b) for b in ('practical','executive','technical')]+[
            dict(scope='global',category='claim-controls',content='CONTROL '*2000+' CONTROL END',claim_status='verified'),
            dict(scope='job',job_id=2,category='referral',content='OTHER JOB SECRET',claim_status='context_only',confidentiality='job_confidential',external_id='private'),
        ]
    def test_role_selection(self):
        for title,expected in [('IT Director','practical'),('Infrastructure Manager','practical'),('VP Technology','executive'),('Head of IT','executive'),('Principal Engineer','technical'),('Cloud Architect','technical')]:
            self.assertEqual(select_baseline(title),expected)
        self.assertEqual(select_baseline('Principal Engineer','executive'),'executive')
        with self.assertRaises(ValueError): select_baseline('X','bogus')
    def test_long_context_and_privacy(self):
        context=assemble_context('A'*60000+' CANONICAL END',self.rows(),{'id':1,'title':'IT Director'},'stale')
        payload=context_payload(context)
        self.assertTrue(payload['canonical_profile'].endswith('CANONICAL END'))
        self.assertTrue(payload['mandatory_claim_controls'].endswith('CONTROL END'))
        self.assertEqual(payload['selected_resume_baseline']['content'],'practical COMPLETE END')
        self.assertNotIn('OTHER JOB SECRET',str(context))
        self.assertNotIn('technical COMPLETE END',str(context))
    def test_factual_allowlist(self):
        canonical='- Legal name: Example Person\n- W2/job-search email: person@example.test'
        self.assertEqual(supplemental_tier('What is your name?\nEmail address?',canonical),'small')
        for text in ['Why hire you?','What is your name? Explain your architecture.','What is your salary?','Are you disabled?','']:
            self.assertEqual(supplemental_tier(text,canonical),'intensive')
        self.assertEqual(supplemental_tier('Email address?',''),'intensive')
    def test_overqualified_not_skip(self):
        result=llm._fallback_score({'title':'Helpdesk Tier 1'},'IT support')
        self.assertEqual(result.qualification_band,'Overqualified')
        self.assertNotEqual(result.recommendation,'Skip')
    def test_missing_baseline_blocks_package(self):
        context=assemble_context('Profile',[],{'id':1,'title':'IT Director'})
        with self.assertRaises(ValueError): llm.generate_application_package({},context)
    def test_routing_and_fallback(self):
        providers=[dict(name='openai',model='old',api_key='test'),dict(name='claude',model='existing-claude',api_key='test')]
        reset_trace()
        with patch.object(llm,'_configured_providers',return_value=providers), patch.object(llm,'_generate_openai_structured',side_effect=RuntimeError('private request body')), patch.object(llm,'_generate_claude_structured',return_value='ok') as fallback:
            self.assertEqual(llm._generate_structured('system',{},llm.ApplicationPackage,task='package'),'ok')
            self.assertEqual(fallback.call_args.args[0]['model'],'existing-claude')
        trace=get_trace()
        self.assertEqual(trace[0]['model'],'gpt-6.1-sol')
        self.assertTrue(trace[-1]['fallback'])
        self.assertNotIn('private request body',json.dumps(trace))
    def test_responses_parameters_and_usage(self):
        client=MagicMock()
        client.responses.parse.return_value=SimpleNamespace(model='gpt-6-luna',usage=SimpleNamespace(model_dump=lambda:{'input_tokens':10}),output=[SimpleNamespace(type='message',content=[SimpleNamespace(parsed='parsed')])])
        providers=[dict(name='openai',model='old',api_key='test')]
        reset_trace()
        with patch.object(llm,'_configured_providers',return_value=providers),patch.object(llm,'_client',return_value=client):
            self.assertEqual(llm._generate_structured('system',{},llm.ApplicationPackage,tier='small'),'parsed')
        args=client.responses.parse.call_args.kwargs
        self.assertEqual(args['model'],'gpt-6-luna')
        self.assertEqual(args['reasoning'],{'effort':'low'})
        self.assertEqual(get_trace()[0]['usage'],{'input_tokens':10})
    def test_package_receives_full_baseline(self):
        context=assemble_context('C'*60000,self.rows(),{'id':1,'title':'Principal Engineer'})
        output=llm.ApplicationPackage(resume_title='R',resume_markdown='R',cover_letter_title='C',cover_letter_markdown='C')
        with patch.object(llm,'_configured_providers',return_value=[{}]),patch.object(llm,'_generate_structured',return_value=output) as generate:
            llm.generate_application_package({'title':'Principal Engineer'},context)
        payload=generate.call_args.args[1]['career_context']
        self.assertEqual(payload['selected_resume_baseline']['content'],'technical COMPLETE END')
        self.assertEqual(len(payload['canonical_profile']),60000)
        self.assertEqual(generate.call_args.kwargs['task'],'package')
    def test_unparsed_response_fails(self):
        client=MagicMock();client.responses.parse.return_value=SimpleNamespace(model='x',usage=None,output=[])
        with patch.object(llm,'_client',return_value=client):
            with self.assertRaises(RuntimeError):llm._generate_openai_structured({'model':'gpt-6.1-sol','reasoning':'medium'},'system',{},llm.ApplicationPackage)

if __name__=='__main__':unittest.main()
