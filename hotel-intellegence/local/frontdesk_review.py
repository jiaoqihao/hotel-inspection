"""Inspect one gesture target. Ask the local VLM one observable question at a time."""
from PIL import Image, ImageStat
import base64
import io
import json

from local.vision_model import ask_vision, object_schema

OK, ISSUE, UNKNOWN = '未见异常', '发现异常', '无法判断'


def finding(name, status, detail):
    return dict(name=name, status=status, detail=detail, level='需要复核' if status == ISSUE else '')


def choose(pictures, prompt, letters, standard):
    if standard:
        prompt += '\n门店现场标准：' + json.dumps(standard, ensure_ascii=False)
    schema = object_schema({'choice': {'type':'string', 'enum':list(letters)}})
    letter = ask_vision(prompt, pictures, schema, 32)['choice']
    if letter not in list(letters):
        raise ValueError('模型未返回有效的仪容观察，请重试')
    return letter


def check_person(paths, tie_style, hair_style, standard):
    pictures = []
    for path in paths:
        with Image.open(path) as im:
            if min(im.size) < 64 or ImageStat.Stat(im.convert('L')).stddev[0] < 4:
                continue
            im.thumbnail((1000, 1000))
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=92)
        pictures.append('data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii'))
    if not pictures:
        return '款式不明', [finding(name, UNKNOWN, '人物局部过小或画面缺少可辨细节。')
                         for name in ('工服','胸牌','领带','扎发')]

    style = '款式不明'
    uniform = finding('工服', UNKNOWN, '工服款式依据不足，请核对门店款式与上衣细节。')
    if (tie_style or hair_style) and tie_style != hair_style:
        choice = choose(pictures, '按外套剪裁、领型和颜色选择证据支持的工服款式，忽略人的性别、领带和头发。'
                        f'A={tie_style or "未配置"}。B={hair_style or "未配置"}。'
                        'C=上衣清楚且与两款均不符。D=画面不足以核实或款式无法区分。', 'ABCD', standard)
        if choice == 'A' and tie_style:
            style = '领带款'
            uniform = finding('工服', OK, '画面中上衣符合领带款特征：' + tie_style)
        elif choice == 'B' and hair_style:
            style = '扎发款'
            uniform = finding('工服', OK, '画面中上衣符合扎发款特征：' + hair_style)
        elif choice == 'C' and tie_style and hair_style:
            uniform = finding('工服', ISSUE, '可辨认的上衣与门店填写的两种工服款式均不符。')

    badge = choose(pictures, '仔细看外套胸前：是否有一块独立别在上衣上的矩形员工名牌？'
                   '口袋、衣服金边、扣子和压线不算名牌。'
                   'A 胸前有一块独立的名牌，B 清晰的胸前衣料上没有名牌，C 遮挡或模糊无法核实。', 'ABC', standard)
    if badge == 'A':
        placement = choose(pictures, '检查胸前名牌是否妥帖。A=佩戴在胸前且固定平整；'
                           'B=明显歪斜、松脱或错位；C=角度或画质不足以判断。', 'ABC', standard)
        status, detail = {'A':(OK,'胸前可见胸牌，佩戴位置端正。'), 'B':(ISSUE,'胸牌明显歪斜、松脱或位置不当。'),
                          'C':(UNKNOWN,'可见胸牌，佩戴角度或位置看不清。')}[placement]
    elif badge == 'B':
        status, detail = ISSUE, '胸前佩戴区域清晰可见，未见胸牌。'
    else:
        status, detail = UNKNOWN, '胸前佩戴区域被遮挡或不清晰。'
    findings = [uniform, finding('胸牌',status,detail)]
    if style == '领带款':
        tie = choose(pictures, '检查衬衫领口。A=佩戴领带且领结在领口中央；B=清楚看到空领口，未戴领带；'
                     'C=领结明显歪斜或松脱；D=遮挡或细节看不清。', 'ABCD', standard)
        status, detail = {'A':(OK,'领口可见领带，领结居中。'), 'B':(ISSUE,'领口清楚可见，未戴领带。'),
                          'C':(ISSUE,'领结明显歪斜或松脱。'), 'D':(UNKNOWN,'领口或领结细节看不清。')}[tie]
        findings.append(finding('领带',status,detail))
    elif style == '扎发款':
        hair = choose(pictures, 'Which describes the hair? A = clearly tied back. B = clearly shorter than shoulders. '
                      'C = shoulder-length or longer hair clearly hanging loose. D = cannot tell.', 'ABCD', standard)
        status, detail = {'A':(OK,'可见头发已束起。'), 'B':(OK,'头发短于肩部，短发免扎。'),
                          'C':(ISSUE,'可见齐肩或更长的头发披散。'), 'D':(UNKNOWN,'头发长度或束发状态看不清。')}[hair]
        findings.append(finding('扎发',status,detail))
    else:
        findings.extend(finding(name, UNKNOWN, '工服款式依据不足，请复核适用要求。') for name in ('领带','扎发'))
    return style, findings
