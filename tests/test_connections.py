import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from saha_agent.connections import Connections
from saha_agent.store import Store


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.runtime = Mock(store=self.store)
        self.runtime.config = {'integrations': {'clickup': {'workspace_id': '12345', 'user_id': '67890'}}}
        self.c = Connections(self.runtime)

    def task(self, revision='1', status='open', owner=67890):
        return {'id': 'abc', 'name': 'Fix a bug', 'date_updated': revision,
                'status': {'status': status}, 'assignees': [{'id': owner}]}

    def test_first_scan_is_baseline_then_only_changed_eligible_owned_tasks_enqueue(self):
        self.c.accept_tasks([self.task()])
        self.runtime.ingest_snapshot.assert_not_called()
        self.c.accept_tasks([self.task()])
        self.runtime.ingest_snapshot.assert_not_called()
        self.c.accept_tasks([self.task('2')])
        self.runtime.ingest_snapshot.assert_called_once()
        self.c.accept_tasks([self.task('3', 'qa complete'), self.task('4', owner=22)])
        self.assertEqual(self.runtime.ingest_snapshot.call_count, 1)

    def test_failed_ingestion_does_not_advance_revision(self):
        self.c.accept_tasks([self.task()])
        self.runtime.ingest_snapshot.side_effect = RuntimeError('disk full')
        with self.assertRaises(RuntimeError): self.c.accept_tasks([self.task('2')])
        self.runtime.ingest_snapshot.side_effect = None
        self.c.accept_tasks([self.task('2')])
        self.assertEqual(self.runtime.ingest_snapshot.call_count, 2)

    def test_owner_memories_are_scoped_and_revocable(self):
        m = self.store.remember('clickup', 'Include a rollback plan.')
        self.assertEqual(len(self.store.memories('clickup')), 1)
        self.assertEqual(self.store.memories('github'), [])
        self.store.forget(m['id'])
        self.assertEqual(self.store.memories('clickup'), [])

    def test_feedback_invalidates_running_attempt_and_requeues(self):
        rule={'id':'local','source':'local','instructions':'work','enabled':True}
        event={'source':'local','id':'x','correlation':'x','title':'x','text':'x','context':{}}
        tid=self.store.ingest(event,rule,'demo')['task_id']
        task=self.store.claim(60,20)
        self.store.feedback(tid,'Use the second approach.')
        self.assertFalse(self.store.finish(task, {'summary':'stale'}, 'ready_for_review'))
        next_task=self.store.claim(60,20)
        self.assertEqual(next_task['revision'],2)
        self.assertEqual(next_task['owner_feedback'][0]['text'],'Use the second approach.')

if __name__ == '__main__': unittest.main()
