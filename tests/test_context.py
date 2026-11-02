import json
from unittest import TestCase
from unittest.mock import patch
from saha_agent.context_mcp import call
from saha_agent.engines import run
from saha_agent import engines
from saha_agent.jev import score

CFG={'integrations':{'clickup':{'enabled':True,'user_id':'1'},'github':{'enabled':True}},'responsibilities':[{'source':'github','repositories':['owner/repo']}]}

class ContextTests(TestCase):
    def test_unassigned_task_is_not_exposed(self):
        with patch('saha_agent.connections.Connections.clickup',return_value={'assignees':[{'id':2}]}):
            with self.assertRaisesRegex(ValueError,'assignment scope'):call(CFG,'clickup_task',{'task_id':'abc'})

    def test_arbitrary_api_path_and_repo_are_rejected(self):
        with self.assertRaises(ValueError):call(CFG,'clickup_task',{'task_id':'../../user'})
        with self.assertRaises(ValueError):call(CFG,'github_pr',{'repository':'other/private','number':1})
        with self.assertRaises(ValueError):call(CFG,'delete_task',{'task_id':'abc'})

    def test_fresh_pr_head_is_used_for_checks(self):
        with patch('saha_agent.connections.Connections.gh',side_effect=[{'head':{'sha':'current'}},[],[],{}]) as gh:
            call(CFG,'github_pr',{'repository':'owner/repo','number':1})
            self.assertIn('commits/current/check-runs',gh.call_args.args[0])

    def test_jev_disabled_does_not_access_credentials_or_network(self):
        self.assertIsNone(score('test',{'enabled':False}))

    def test_team_verifies_review_feedback_with_one_bounded_rework_round(self):
        ready={'status':'ready','summary':'done','question':'','evidence':[]}
        blocked={**ready,'status':'blocked','summary':'reproduce this finding'}
        outer=engines.run
        with patch('saha_agent.engines.run',side_effect=[ready,blocked,ready,ready]) as worker:
            result=outer({'engine':'team','result':None},None,{'timeout_seconds':60})
            self.assertEqual(worker.call_count,4)
            self.assertEqual(result['status'],'ready')
            self.assertTrue(worker.call_args_list[1].args[2]['review_only'])
            self.assertIn('review_findings_to_verify',worker.call_args_list[2].args[0]['result'])

    def test_team_stops_for_owner_question_instead_of_looping(self):
        waiting={'status':'needs_input','summary':'Need a design','question':'Which design?','evidence':[]}
        outer=engines.run
        with patch('saha_agent.engines.run',return_value=waiting) as worker:
            result=outer({'engine':'team','result':None},None,{'timeout_seconds':60})
            self.assertEqual(worker.call_count,1)
            self.assertEqual(result['status'],'needs_input')
