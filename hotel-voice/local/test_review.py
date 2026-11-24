import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

TEMP = tempfile.TemporaryDirectory()
os.environ['HOTEL_VOICE_DATA'] = TEMP.name
from local.server import grounded_report, analyze_segments
from local.rules import rule_report

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.segments = [{'id':0,'start':0,'end':20,'text':'您好，欢迎光临。请问有没有忌口？我去厨房确认出餐进度。'}]

    def test_fabricated_evidence_is_discarded(self):
        report=grounded_report({'checks':[{'name':'错误结论','status':'观察到','evidence':[{'segment':0,'quote':'客户非常满意'}]}]},self.segments)
        self.assertEqual(report['checks'],[])

    def test_task_owner_and_due_require_exact_evidence(self):
        report=grounded_report({'todos':[{'title':'确认出餐','owner':'张经理','due':'明天','evidence':[{'segment':0,'quote':'我去厨房确认出餐进度。'}]}]},self.segments)
        self.assertEqual(report['todos'][0]['owner'],'待确认')
        self.assertEqual(report['todos'][0]['due'],'待确认')
        self.assertEqual(report['todos'][0]['evidence'][0]['start'],0)

    def test_missing_speech_is_not_service_failure(self):
        report=rule_report(self.segments)
        self.assertEqual(report['scene'],'餐厅服务')
        missing=next(c for c in report['checks'] if c['name']=='订单复核')
        self.assertEqual(missing['status'],'无法判断')
        self.assertEqual(missing['evidence'],[])
        self.assertTrue(all(c['status']!='已完成' for c in report['checks']))

    def test_no_hotel_terms_remains_unknown(self):
        report=rule_report([{'id':0,'start':0,'text':'今天天气很好。'}])
        self.assertEqual(report['scene'],'其他')
        self.assertEqual(report['todos'],[])

    def test_order_repetition_is_not_a_future_task(self):
        report=rule_report([{'id':0,'start':0,'text':'我帮您确认一下，一份鱼，两份米饭。'}], '餐厅服务')
        self.assertEqual(report['todos'],[])

    def test_meeting_explicit_assignment_is_preserved(self):
        report=rule_report([{'id':0,'start':0,'text':'李经理负责联系维修人员，周五前提交维修报价。请把会议纪要发给各部门。'}], '会议')
        self.assertEqual(len(report['todos']),2)
        self.assertEqual(report['todos'][0]['owner'],'李经理')

    def test_overlapping_words_excluded_from_rules(self):
        segments=self.segments+[{'id':1,'start':21,'text':'明天联系客户。','overlap':True}]
        with patch('local.server.model_status',return_value={'semantic':False}):
            report=analyze_segments(segments,{'sceneHint':'餐厅服务'})
        self.assertEqual(report['excludedSegments'],1)
        self.assertIn('已排除',report['scope'])
        for kind in ('checks','risks','todos','insights'):
            self.assertTrue(all(ev['segment']==0 for row in report[kind] for ev in row['evidence']))

    def test_output_limit_keeps_report_with_disclosed_fallback(self):
        self.segments[0].update(speaker='说话人 A',speakerId='spk_0')
        session=MagicMock()
        session.post.return_value.json.return_value={'choices':[{'finish_reason':'length'}]}
        with patch('local.server.model_status',return_value={'semantic':True}), patch('local.server.requests.Session',return_value=session), patch('local.server.update'):
            report=analyze_segments(self.segments,{'id':'test','sceneHint':'餐厅服务'})
        self.assertEqual(report['mode'],'rules')
        self.assertEqual(report['fallbackBatches'],1)
        self.assertIn('长度限制',report['scope'])
        self.assertTrue(report['checks'])
        import json
        sent=json.loads(session.post.call_args.kwargs['json']['messages'][1]['content'])
        self.assertNotIn('speaker',sent['segments'][0])
        self.assertNotIn('speakerId',sent['segments'][0])

if __name__ == '__main__':
    unittest.main()
