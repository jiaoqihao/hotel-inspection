import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

TEMP = tempfile.TemporaryDirectory()
os.environ['HOTEL_VOICE_DATA'] = TEMP.name
from local.badge_analysis import AnalysisError, FIELDS, coverage, analyze_segments, grounded_report, request_reports
from local.rules import rule_report

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        redaction = patch("local.badge_privacy.redact_locally", side_effect=lambda session, text: text.replace("王某某", "＊"))
        self.redact = redaction.start()
        self.addCleanup(redaction.stop)
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

    def test_remote_input_excludes_overlap_and_archival_metadata(self):
        segments=self.segments+[{'id':1,'start':21,'text':'明天联系客户。','overlap':True}]
        self.segments[0].update(speaker='说话人 A', speakerId='spk_0')
        raw=dict(scene='餐厅服务', **{k:[] for k in FIELDS})
        raw['conclusions']=[dict(value='录音提到迎宾。', evidence=[0])]
        published=[]
        with patch('local.badge_analysis.classify',return_value=['餐厅服务']), patch('local.badge_analysis.request_reports', side_effect=lambda _: [raw] if published else self.fail('须先发布场景')) as request:
            report=analyze_segments(segments,{'sceneHint':'会议','employee':'归档名称'},on_scenes=published.append)
        self.assertEqual(published,[['餐厅服务']])
        sent = request.call_args.args[0]
        self.assertEqual(set(sent), {'scenes','segments'})
        self.assertIn('欢迎光临', ''.join(q['text'] for q in sent['segments']))
        self.assertTrue(all(set(q)=={'ref','text'} for q in sent['segments']))
        self.assertEqual(report['excludedSegments'],1)
        self.assertEqual(report['summary'],'录音提到迎宾。')
        self.assertEqual(report['mode'],'deepseek')
        self.assertEqual(report['conclusions'][0]['evidence'][0]['start'],0)

    def test_private_details_never_reach_either_remote_stage_or_evidence(self):
        from local.badge_privacy import redacted_turns
        words="我叫王某某，我的工资是三万，婚宴服务联系人王某某手机号13812345678。请把婚宴报价发给王某某。"
        original=[dict(id=4,start=10,text=words)]
        excerpts=list(redacted_turns(original, []))
        self.assertTrue(excerpts)
        sent=[]
        def respond(data,prompt,field,max_tokens):
            sent.append(data)
            if field=="scenes": return ["销售沟通"]
            return [dict(scene="销售沟通",**{k:([dict(value="沟通婚宴报价",evidence=[0])] if k=="conclusions" else []) for k in FIELDS})]
        with patch("local.badge_analysis.request_json",side_effect=respond):
            result=analyze_segments(original,{})
        self.assertEqual(len(sent),2)
        for private in ("王某某","13812345678","工资","三万","联系人"):
            self.assertNotIn(private,str(sent))
            self.assertNotIn(private,str(result))
        for ev in result["conclusions"][0]["evidence"]:
            self.assertIn(ev["quote"],words)
            self.assertEqual(ev["start"],10)

    def test_classification_receives_conversation_without_business_keywords(self):
        turns = ["我定了一间房。", "好的好的，现在就把房卡给你。", "嗯，那就给我这个吧。"]
        segments = [dict(id=i, start=i*3, text=text) for i, text in enumerate(turns)]
        def remote(data, prompt, field, max_tokens):
            self.assertEqual([s["text"] for s in data["segments"]], turns)
            if field == "scenes":
                return ["销售沟通"]
            return [dict(scene="销售沟通", **{k:([dict(value="正在办理入住。",evidence=[1])] if k=="conclusions" else []) for k in FIELDS})]
        with patch("local.badge_analysis.request_json",side_effect=remote):
            report = analyze_segments(segments,{})
        self.assertEqual(report["scene"], "销售沟通")
        self.assertEqual(report["conclusions"][0]["evidence"][0]["start"], 3)

    def test_remote_decides_scene_even_when_another_scene_is_mentioned(self):
        with patch("local.badge_analysis.request_json",return_value=["会议"]), patch("local.badge_analysis.request_reports",return_value=[dict(scene="会议", **{k:[] for k in FIELDS})]):
            report=analyze_segments([dict(id=0,start=0,text="餐厅场景识别测试。")],{})
        self.assertEqual(report["scenes"],["会议"])

    def test_inconclusive_model_result_is_not_overridden_by_upload_hint(self):
        with patch("local.badge_analysis.request_json",return_value=[]) as classify, patch("local.badge_analysis.request_reports") as detail:
            report=analyze_segments([dict(id=0,start=0,text="嗯，好的。")],{"sceneHint":"会议"})
        classify.assert_called_once()
        detail.assert_not_called()
        self.assertEqual(report["scenes"],[])
        self.assertEqual(report["sceneSource"],"业务依据不足")

    def test_unverified_redaction_stops_before_deepseek(self):
        self.redact.side_effect=lambda *_: "模型改写了录音。"
        with patch("local.badge_analysis.request_json") as remote, self.assertRaisesRegex(ValueError,"脱敏"):
            analyze_segments(self.segments,{})
        remote.assert_not_called()

    def test_malformed_private_clause_does_not_block_another_turn(self):
        self.redact.side_effect=lambda _,text: "脱敏改写" if "片段甲" in text else text
        segments=[dict(id=0,start=0,text="片段甲。"),dict(id=1,start=3,text="现在就把房卡给你。")]
        with patch("local.badge_analysis.request_json",return_value=["销售沟通"]) as classify, patch("local.badge_analysis.request_reports",return_value=[dict(scene="销售沟通",**{k:[] for k in FIELDS})]):
            report=analyze_segments(segments,{})
        self.assertEqual(report["unverifiedClauses"],1)
        self.assertEqual(classify.call_args.args[0]["segments"],[{"ref":0,"text":"现在就把房卡给你。"}])

    def test_private_chat_is_not_summarized(self):
        with patch("local.badge_analysis.classify") as classify, patch("local.badge_analysis.request_reports") as report:
            result=analyze_segments([dict(id=0,start=0,text="我妈妈生病了。我的工资涨了。")],{})
        classify.assert_not_called()
        report.assert_not_called()
        self.assertIsNone(result["scene"])
        self.assertEqual(result["todos"],[])
        self.assertNotIn("工资",str(result))

    def test_declared_short_scenes_and_misheard_canteen(self):
        for text, expected in [
            ("食团场景识别测试。食堂场景识别测试。", ["餐厅服务"]),
            ("销售场景识别测试。", ["销售沟通"]),
            ("会议场景识别测试。销售场景测试。", ["会议", "销售沟通"]),
        ]:
            with self.subTest(text=text), patch("local.badge_analysis.request_json",return_value=expected) as remote:
                r=analyze_segments([dict(id=1,start=2,text=text)],{})
                self.assertEqual(r["scenes"],expected)
                self.assertEqual(r["scene"],expected[0] if len(expected)==1 else "混合场景")
                self.assertFalse(r["todos"] or r["risks"] or r["flow"])
                remote.assert_called_once()
                self.assertEqual(r["conclusions"][0]["evidence"][0]["start"],2)

    def test_deprecated_scene_is_never_a_classifier_result(self):
        with patch("local.badge_analysis.request_json",return_value=["其他"]):
            with self.assertRaises(AnalysisError):
                analyze_segments(self.segments,{})

    def test_invalid_classifier_response_stops_before_report(self):
        with patch('local.badge_analysis.request_json', return_value=['未知分类']), patch('local.badge_analysis.request_reports') as report:
            with self.assertRaises(AnalysisError):
                analyze_segments(self.segments,{})
        report.assert_not_called()

    def test_report_cannot_change_published_scene(self):
        raw=dict(scene='会议', **{k:[] for k in FIELDS})
        published=[]
        with patch('local.badge_analysis.classify',return_value=['餐厅服务']), patch('local.badge_analysis.request_reports',return_value=[raw]):
            with self.assertRaises(AnalysisError):
                analyze_segments(self.segments,{},on_scenes=published.append)
        self.assertEqual(published,[['餐厅服务']])

    def test_truncated_or_invalid_api_result_fails_without_fallback(self):
        res=MagicMock(status_code=200)
        with patch.dict(os.environ, DEEPSEEK_API_KEY='synthetic'), patch('local.badge_analysis.requests.post', return_value=res):
            for choice in [{'finish_reason':'length'}, {'finish_reason':'stop','message':{'content':'bad'}}]:
                res.json.return_value={'choices':[choice]}
                with self.assertRaises(AnalysisError):
                    request_reports({'scenes':['会议']})

    def test_cross_batch_merge_and_mixed_scenes(self):
        segments=[dict(id=i,start=i*20,text='服务培训'*1600+'请明天报价') for i in range(2)]
        def reply(scene):
            return dict(scene=scene, **{k:[] for k in FIELDS})
        sales=reply('销售沟通')
        sales['todos']=[dict(title='报价', evidence=[0])]
        from copy import deepcopy
        later=deepcopy(sales)
        later['todos'][0]['evidence']=[1]
        meeting=reply('会议')
        with patch('local.badge_analysis.classify',side_effect=[['销售沟通'],['销售沟通','会议']]), patch('local.badge_analysis.request_reports',side_effect=[[deepcopy(sales)],[later,meeting],[sales,meeting]]) as call:
            report=analyze_segments(segments,{})
        self.assertEqual(call.call_count,3)
        self.assertIn('候选报告',call.call_args.args[0])
        self.assertEqual((report['scene'],report['batchCount'],len(report['todos'])),('混合场景',2,1))
        self.assertEqual(report['todos'][0]['scene'],'销售沟通')

    def test_unmentioned_requirements_stay_visible_without_inventing_evidence(self):
        report={k:[] for k in FIELDS}
        report["insights"]=[dict(scene="销售沟通",label="标签建议"),dict(scene="会议",label="议题")]
        report["risks"]=[dict(scene="餐厅服务",complaint=False)]
        sections=coverage(["销售沟通","餐厅服务"],report)
        sales,cafe=({item["name"]:item["matches"] for item in section["items"]} for section in sections)
        self.assertEqual(sales["标签建议"], [0])
        self.assertEqual(sales["客户画像线索"], [0])
        self.assertEqual(sales["竞品提及"], [])
        self.assertEqual(cafe["客诉预警"], [])
        self.assertEqual(cafe["说菜"], [])

    def test_no_eligible_segments_make_no_remote_request(self):
        with patch('local.badge_analysis.request_reports') as call:
            result=analyze_segments([dict(self.segments[0],overlap=True)],{})
        call.assert_not_called()
        self.assertEqual(result['mode'],'noEvidence')

if __name__ == '__main__':
    unittest.main()
