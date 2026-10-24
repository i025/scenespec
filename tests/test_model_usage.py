"""A real local relay request with a fake upstream; never contacts the model."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
spec = importlib.util.spec_from_file_location('usage_relay', SCRIPTS/'algorithm_proposal_relay.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class ModelUsageTests(unittest.TestCase):
    def test_incomplete_response_usage_is_recorded_without_credentials(self):
        usage = {'input_tokens':31, 'output_tokens':8, 'total_tokens':39}
        event = {'type':'response.incomplete', 'response':{'model':relay.MODEL,
                 'status':'incomplete', 'usage':usage}}
        upstream = Mock(status=200)
        upstream.getheader.return_value = 'text/event-stream'
        upstream.read1.side_effect = [('data: '+json.dumps(event)+'\n\n').encode(), b'']
        with tempfile.TemporaryDirectory() as temp, patch.object(relay.urllib.request, 'urlopen', return_value=upstream):
            root = Path(temp)
            auth = root/'auth.json'
            auth.write_text(json.dumps({'tokens':{'access_token':'fake-secret', 'account_id':'fake-account'}}))
            server = relay.serve(root/'model.sock', auth, root/'record.json')
            connection = relay.UnixConnection(str(root/'model.sock'))
            try:
                connection.request('POST', '/responses', json.dumps({'model':relay.MODEL}))
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(b'response.incomplete', response.read())
            finally:
                connection.close()
                server.shutdown(); server.server_close()
            text = (root/'record.json').read_text()
            record = json.loads(text)
            self.assertEqual(record['usage'], usage)
            self.assertEqual(record['response_status'], 'incomplete')
            self.assertEqual(record['upstream_calls'], 1)
            self.assertNotIn('fake-secret', text)
            self.assertNotIn('fake-account', text)


if __name__ == '__main__':
    unittest.main()
