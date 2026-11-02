"""Offline replay of real overload streams; no service calls or candidate execution."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import run_m3_reliable as exp
from tests.test_m3_reliable import Backend, Clock


ROOT=Path(__file__).resolve().parents[1]
ERROR=ROOT/'experiments/m3_reliable/results/elite_centroid/M3/r0/coding/attempt0/response_0.sse'


class StreamBackend(Backend):
    def __call__(self,task,folder,budget):
        result=super().__call__(task,folder,budget)
        if result['status']=='failed':
            text=ERROR.read_text() if self.response is None else self.response
            (folder/'response_0.sse').write_text(text)
        return result

    def __init__(self,clock,steps,response=None):
        super().__init__(clock,steps);self.response=response


def failure(headers,code='server_is_overloaded'):
    return 'data: '+json.dumps({'type':'response.failed','response':{'error':{'code':code,'headers':headers},'output':[]}})+'\n'


class StreamCapacityTests(unittest.TestCase):
    def run_steps(self,steps,response=None,enabled=True):
        with TemporaryDirectory() as tmp:
            clock=Clock();backend=StreamBackend(clock,steps,response)
            folder=Path(tmp)/'coding'
            result=exp.run_logical({'files':{'algorithm.py':'original'}},folder,backend,
                                   sleep=clock.sleep,clock=clock,retry_stream_overload=enabled)
            return result,exp.read(folder/'logical.json'),backend.budgets,clock.waits

    def test_real_stream_replay_honors_327_seconds_and_cumulative_budgets(self):
        result,ledger,budgets,waits=self.run_steps([{'http':200},{'success':True}])
        self.assertEqual(result['status'],'completed');self.assertEqual(sum(waits),327)
        self.assertLessEqual(max(waits),30)
        self.assertEqual(budgets[1]['wall_seconds'],263)
        self.assertEqual(budgets[1]['total_tokens'],320000)
        self.assertEqual(budgets[1]['model_requests'],5)
        self.assertEqual(budgets[1]['input_bytes_total'],1047576)
        self.assertEqual(ledger['unknown_usage_requests'],1);self.assertIsNone(ledger['actual_total_tokens'])
        self.assertEqual(ledger['charged_tokens'],155000)
        self.assertEqual(ledger['attempts'][0]['retry_after_headers']['retry-after'],'327')

    def test_historical_default_still_does_not_retry_http_200(self):
        result,ledger,budgets,waits=self.run_steps([{'http':200}],enabled=False)
        self.assertEqual(result['status'],'failed');self.assertEqual(len(budgets),1);self.assertEqual(waits,[])

    def test_second_long_server_wait_cannot_fit_600_second_logical_budget(self):
        result,ledger,budgets,waits=self.run_steps([{'http':200},{'http':200}])
        self.assertEqual(result['status'],'service_capacity_failed')
        self.assertEqual(len(budgets),2);self.assertEqual(sum(waits),327)
        self.assertEqual(ledger['unknown_usage_requests'],2)

    def test_maximum_two_retries_with_short_delays(self):
        result,ledger,budgets,waits=self.run_steps([{'http':200}]*3,failure({'retry-after':'1'}))
        self.assertEqual(len(budgets),3);self.assertEqual(sum(waits),90)
        self.assertEqual(result['status'],'service_capacity_failed')

    def test_missing_malformed_or_unaffordable_delay_is_capacity_failure_without_retry(self):
        for headers in ({},{'retry-after':'bad'},{'retry-after':'nan'},
                        {'retry-after':'-1'},{'retry-after':'600'}):
            with self.subTest(headers=headers):
                result,_,budgets,waits=self.run_steps([{'http':200}],failure(headers))
                self.assertEqual(result['status'],'service_capacity_failed')
                self.assertEqual(len(budgets),1);self.assertEqual(waits,[])

    def test_exhausted_token_request_or_input_budget_prevents_waiting(self):
        for limits in ({'total_tokens':80000},{'model_requests':1},{'input_bytes_total':1000}):
            with patch.dict(exp.BUDGET,limits):
                result,_,budgets,waits=self.run_steps([{'http':200}])
            self.assertEqual(result['status'],'service_capacity_failed');self.assertEqual(waits,[])

    def test_no_retry_for_edits_items_other_errors_or_later_request(self):
        for step,response in (({'http':200,'edited':True},None),({'http':200,'item':True},None),
                              ({'http':200,'requests':2},None),({'http':200},failure({'retry-after':'1'},'other')),
                              ({'http':500},None),
                              ({'http':200},'data: {"type":"response.output_item.added","item":{}}\n'+failure({'retry-after':'1'}))):
            with self.subTest(step=step,response=response):
                result,_,budgets,waits=self.run_steps([step],response)
                self.assertEqual(result['status'],'failed');self.assertEqual(len(budgets),1);self.assertEqual(waits,[])

    def test_retry_after_units_case_and_http_date(self):
        now=datetime(2026,11,2,tzinfo=timezone.utc)
        self.assertEqual(exp.retry_after_seconds({'Retry-After':'30','retry-after-ms':'327000'},now),327)
        self.assertEqual(exp.retry_after_seconds({'Retry-After':'Mon, 02 Nov 2026 00:02:00 GMT'},now),120)

    def test_real_second_failure_reports_320_second_delay(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp);source=ROOT/'experiments/m3_reliable/results/axis_target/M2/r1/coding/attempt0'
            for name in ('events.jsonl','response_0.sse','candidate.py','candidate.diff'):
                (p/name).write_bytes((source/name).read_bytes())
            result=exp.read(source/'backend_result.json');requests=exp.read(source/'model_calls.json')['requests']
            original=exp.read(source.parent.parent/'task.json')['files']['algorithm.py']
            parsed=exp.stream_overload(result,p,original,requests)
            self.assertEqual(exp.retry_after_seconds(parsed['headers'],datetime.now(timezone.utc)),320)


if __name__=='__main__':unittest.main()
