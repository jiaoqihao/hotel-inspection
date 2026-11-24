"""Conservative extractive fallback. No generative model, identity or sentiment inference."""
import re

SCENE_TERMS = {
    '餐厅服务': r'用餐|点菜|菜品|菜单|忌口|过敏|上菜|出餐|清蒸|米饭|餐具|餐位|催菜',
    '销售沟通': r'报价|预算|婚宴|签约|合同|定金|竞品|场地|套餐|桌数|看场|会议室|采购',
    '会议': r'开会|议题|会议纪要|汇报|本周|进度|负责人|分工|决定|项目|落实|复盘',
}
CHECKS = {
    '餐厅服务': [('迎宾问候', r'欢迎光临|您好|你好|晚上好|中午好|早上好'), ('人数与需求询问', r'几位|多少位|几个人|忌口|过敏|有什么要求|有什么需要'), ('菜品推荐', r'推荐|招牌|特色菜|今天的.*菜|口味'), ('订单复核', r'确认一下|核对|重复一下|您点的是|点了'), ('问题回应', r'不好意思|抱歉|对不起|我.*(?:催|确认|核实|处理|反馈)')],
    '销售沟通': [('需求沟通', r'需要|希望|计划|人数|桌数|预算|日期'), ('方案介绍', r'套餐|方案|包含|报价|价格'), ('顾虑回应线索', r'担心|顾虑|太贵|超预算|可以调整|优惠'), ('下一步约定', r'发送|发给|联系|预约|看场|回电|跟进')],
    '会议': [('议题讨论', r'讨论|议题|汇报|关于|今天.*会议'), ('决策表达线索', r'决定|同意|确定采用|确定执行|就按'), ('分工与后续动作', r'负责|安排|落实|完成|提交|跟进')],
    '其他': [],
}

def rule_report(segments, hint='自动识别'):
    text = '\n'.join(s['text'] for s in segments)
    scores = {scene: len(re.findall(pattern, text)) for scene, pattern in SCENE_TERMS.items()}
    ordered = sorted(scores, key=scores.get, reverse=True)
    inferred = ordered[0] if scores[ordered[0]] >= 2 else '其他'
    scene = hint if hint in CHECKS else inferred
    sentences = []
    for segment in segments:
        for match in re.finditer(r'[^。！？!?\n]+[。！？!?]?', segment['text']):
            quote = match.group().strip()
            if len(quote) >= 3:
                sentences.append((quote, {'segment': segment['id'], 'quote': quote, 'start': segment['start']}))
    def matches(pattern, limit=4):
        return [(q,e) for q,e in sentences if re.search(pattern,q)][:limit]
    checks = []
    for name, pattern in CHECKS[scene]:
        found = matches(pattern, 2)
        checks.append({'name': name, 'status': '待复核' if found else '无法判断', 'detail': '发现关键词相关表达，需回听确认语义与说话人。' if found else '未检出对应表达；不能据此判定员工没有执行。', 'evidence': [ev for _,ev in found]})
    risks = []
    for title, pattern in [('等候或处理进度线索', r'等了|久等|还没|太慢|催菜|出餐进度'), ('投诉或不满意线索', r'投诉|不满意|退钱|退款|找经理|不能接受|太差'), ('过敏或特殊需求线索', r'过敏|不能吃|忌口')]:
        found = matches(pattern, 2)
        if found:
            risks.append({'title': title, 'level':'关注', 'detail':'仅检出相关词语，需确认语境、需求是否实际存在及处理情况；不据此推断情绪。', 'evidence':[ev for _,ev in found]})
    todos = []
    for q, ev in matches(r'我(?:会|去|帮|来|负责)|(?:负责|请|安排).{0,25}(?:完成|提交|确认|核实|联系|发送|发给|跟进)|(?:明天|下周|稍后).{0,20}(?:发|联系|反馈)', 6):
        if '确认一下' in q and not re.search(r'稍后|明天|回来|厨房|后续|负责|提交', q):
            continue
        owner = re.search(r'([\u4e00-\u9fff]{1,3}(?:经理|主管|主任))负责', q)
        todos.append({'title':q, 'owner':owner.group(1) if owner else '待确认', 'due':'待确认', 'done':False, 'evidence':[ev]})
    insights = []
    terms = [('需求',r'需要|希望|人数|桌数|预算|日期|计划'),('预算',r'预算|每桌|单价'),('时间',r'明年|下个月|\d+月|周末|周五'),('顾虑',r'担心|顾虑|太贵|超预算|考虑一下'),('竞品',r'别家|其他酒店|对比|竞品'),('决策',r'决定|同意|确定采用|就按'),('意向线索',r'发.*报价|预约|看场|签合同|付定金')]
    for label,pattern in terms:
        for q,ev in matches(pattern,2):
            insights.append({'label':label,'value':q,'evidence':[ev]})
    overview = '本次采用本地规则初筛，按关键词提取原文，不进行生成式推理。'
    if hint == '自动识别':
        overview += f'候选场景：{scene}（规则推测，待复核）。'
    else:
        overview += f'使用你指定的场景：{scene}。'
    overview += '\n原话摘录：\n' + '\n'.join(q for q,_ in sentences[:5])
    return {'scene':scene,'summary':overview,'mode':'rules','scope':'规则初筛不是完整语义分析。未做说话人识别、客户画像或情绪识别；所有命中都需复核，不直接用于员工考核。','checks':checks,'risks':risks,'todos':todos,'insights':insights}
