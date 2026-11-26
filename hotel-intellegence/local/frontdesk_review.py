"""Inspect one gesture target. Ask the local VLM one observable question at a time."""
from PIL import Image, ImageChops, ImageStat
import base64
import io
import json
import re

from local.vision_model import TEXT, ask_vision, encoded_image, object_schema

OK, ISSUE = '未见异常', '发现异常'
MISSING, NO_STANDARD = '取证缺失', '标准缺失'


def finding(name, status, detail):
    if status in (MISSING, NO_STANDARD):
        return dict(name=name, status=ISSUE, detail=detail, level=status)
    return dict(name=name, status=status, detail=detail, level='需要复核' if status == ISSUE else '')


def choose(pictures, prompt, letters, standard):
    if standard:
        prompt += '\n门店现场标准：' + json.dumps(standard, ensure_ascii=False)
    schema = object_schema({'choice': {'type':'string', 'enum':list(letters)}})
    letter = ask_vision(prompt, pictures, schema, 32)['choice']
    if letter not in list(letters):
        raise ValueError('模型未返回有效的仪容观察，请重试')
    return letter


def exposed(image, box):
    # Close-to-black fabric has no recoverable shape, even when the face or monitor is bright.
    w, h = image.size
    region = image.crop(tuple(round(v * scale) for v, scale in zip(box,(w,h,w,h))))
    histogram = region.convert('L').histogram()
    return sum(histogram[:9]) < .6 * sum(histogram)


def visible_badge_area(picture):
    view = ask_vision('Describe the orientation of the person. Is the FRONT LEFT UPPER part of their outer '
                      'garment itself visible, blocked by other objects or turned away? Describe the actual '
                      'view in one short English sentence.', [picture], object_schema({'view':TEXT}), 88)['view']
    return choose([], 'Does this observation positively confirm that the front chest of the outer garment '
                  'can be inspected? A = yes, clearly visible. B = turned away, covered by a hand, hair, '
                  'monitor or another object, partly hidden, or uncertain. Observation: '+view, 'AB','') == 'A'


def garment_clauses(text, pattern):
    # A style description can cover an entire outfit. Keep only the named garment's clauses.
    clauses = re.split(r'[。；;、，,\n]+', text)
    return '、'.join(dict.fromkeys(c.strip().removeprefix('图示参考：') for c in clauses if re.search(pattern, c)))


def jacket_style(pictures, body_context, tie_style, hair_style):
    jackets = []
    for description in (tie_style, hair_style):
        first = description.split('。', 1)[0]
        outer = garment_clauses(first, r'外套|西装|西服|夹克|上衣')
        jackets.append(outer or garment_clauses(first, r'领型|尖领|立领|翻领|收腰|直身|滚边'))
    if jackets[0] == jackets[1]:
        return '款式不明'
    views = [pictures[0], *body_context[:1]]
    choice = choose(views, '只识别举手者的外套款式。忽略内搭、领带、头发和性别。'
                    f'按外套剪裁和颜色选：A={jackets[0] or "未配置"}。B={jackets[1] or "未配置"}。C=画面不足以区分或两者均不符合。',
                    'ABC', '')
    if choice == 'C' or not jackets['AB'.index(choice)]:
        return '款式不明'
    if body_context:
        fitted = ['收腰' in jacket and '不收腰' not in jacket for jacket in jackets]
        if choice in ('A','B') and fitted.count(True) == 1 and fitted['AB'.index(choice)]:
            shape = choose(views, 'Is the outer jacket cut straight or shaped at the waist? '
                           'A = clearly straight or boxy. B = fitted and tapering in at the waist. C = unclear.', 'ABC','')
            if shape != 'B':
                choice = 'C'
        elif choice in ('A','B'):
            reverse = choose(views, '只比较外套颜色与剪裁，忽略内搭、头发和领带。'
                             f'A={jackets[1]}。B={jackets[0]}。C=无法区分或两者均不符合。', 'ABC','')
            if reverse != {'A':'B','B':'A'}[choice]:
                choice = 'C'
    return {'A':'领带款','B':'扎发款','C':'款式不明'}[choice]


def image_data(image):
    buf = io.BytesIO()
    image.convert('RGB').save(buf,format='JPEG',quality=92)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')


def archived_lower_views(scene_path, upper_path):
    """Match the retained upper-body image to a unique full-length pose in the same saved frame."""
    from local.frontdesk_pose import Detector, crop_region, pose_ready, target_box
    if not pose_ready():
        return {}
    with Image.open(scene_path) as scene, Image.open(upper_path) as upper:
        detector = Detector()
        try:
            poses = detector.observe(scene.convert('RGB'), 0)
        finally:
            detector.close()
        reference = upper.convert('RGB').resize((24,32))
        matches = []
        for pose in poses:
            if not pose.body or pose.gesture not in ('left','right','down'):
                continue
            candidate = scene.crop(target_box(pose,scene.size)).convert('RGB').resize(reference.size)
            difference = ImageStat.Stat(ImageChops.difference(candidate,reference)).mean
            if sum(difference)/3 <= 22:
                matches.append(pose)
        if len(matches) != 1:
            return {}
        pose, = matches
        return {name:image_data(crop_region(scene,region)) for name,region in
                [('下装',pose.legs),('皮鞋',pose.shoes)]}


def check_person(paths, tie_style, hair_style, standard, body_context=(), hair_views=()):
    pictures, badge_views, partial_chest = [], [], []
    for path in paths:
        with Image.open(path) as im:
            if min(im.size) < 64 or ImageStat.Stat(im.convert('L')).stddev[0] < 4:
                continue
            if im.format == 'JPEG' and max(im.size) <= 1000:
                picture = encoded_image(path)
            else:
                im.thumbnail((1000, 1000))
                picture = image_data(im)
            if exposed(im,(.33,.42,.65,.75)):
                pictures.append(picture)
            if exposed(im,(.50,.43,.78,.68)):
                if visible_badge_area(picture):
                    badge_views.append(picture)
                else:
                    partial_chest.append(picture)

    style = '款式不明'
    uniform = finding('工服', MISSING, '上身被遮挡、过暗或外套剪裁看不清。')
    if pictures:
        cut = choose(pictures, 'Inspect only the outer garment. A = clearly not a suit blazer: casual jacket, '
                     'sportswear, or just a shirt or sweater without a suit jacket. B = visibly wearing a suit '
                     'blazer, including open or unbuttoned blazers. Use any visible lapels, front panels or sleeves. C = no usable outerwear features visible. '
                     'Ignore the color and style of whatever is underneath.', 'ABC','')
        if cut == 'A':
            uniform = finding('工服', ISSUE, '上身衣着清楚可见，未穿西装外套。')
        elif cut == 'B':
            uniform = finding('工服', OK, '可见已穿西装外套。')
            style = jacket_style(pictures, body_context, tie_style, hair_style)

    badge = choose(badge_views, '仔细看外套胸前：是否有一块独立别在上衣上的矩形员工名牌？'
                   '口袋、衣服金边、扣子和压线不算名牌。'
                   'A 胸前有一块独立的名牌，B 清晰的胸前衣料上没有名牌，C 遮挡或模糊无法核实。', 'ABC', standard) if badge_views else 'C'
    if badge == 'C' and partial_chest:
        visible = choose(partial_chest, '胸前局部被挡住。A=仍能清楚认出固定在本人上衣的独立矩形员工胸牌；'
                         'B=没有足够可见的胸牌特征。口袋或衣服装饰不能算胸牌。', 'AB','')
        if visible == 'A':
            badge, badge_views = 'A', partial_chest
    if badge == 'A':
        placement = choose(badge_views, '检查胸前名牌是否妥帖。A=佩戴在本人左上胸且固定平整；'
                           'B=明显歪斜、松脱或错位；C=角度或画质不足以判断。', 'ABC', standard)
        status, detail = {'A':(OK,'本人左上胸可见胸牌，佩戴端正。'), 'B':(ISSUE,'胸牌明显歪斜、松脱或位置不当。'),
                          'C':(OK,'可见胸牌，露出部分未见明显佩戴异常，角度或完整位置看不清。')}[placement]
    elif badge == 'B':
        status, detail = ISSUE, '胸前佩戴区域清晰可见，未见胸牌。'
    else:
        status, detail = MISSING, '胸前佩戴区域被遮挡或不清晰。'
    findings = [uniform, finding('胸牌',status,detail)]
    if style == '领带款':
        tie = choose(pictures, '检查衬衫领口。A=佩戴领带且领结在领口中央；B=清楚看到空领口，未戴领带；'
                     'C=领结明显歪斜或松脱；D=遮挡或细节看不清。', 'ABCD', standard)
        status, detail = {'A':(OK,'领口可见领带，领结居中。'), 'B':(ISSUE,'领口清楚可见，未戴领带。'),
                          'C':(ISSUE,'领结明显歪斜或松脱。'), 'D':(MISSING,'领口或领结细节看不清。')}[tie]
        findings.append(finding('领带',status,detail))
    elif style == '扎发款' and '发网' in standard:
        hair = choose([*hair_views, *pictures[:1]] if hair_views else pictures, 'Inspect only visible hair across the views of the same person. A = a bun enclosed in a visibly identifiable hairnet. '
                      'B = long hair visibly hanging over shoulders or a clearly exposed loose ponytail. '
                      'C = clearly cropped haircut with the short ends visible at the nape. '
                      'D = hair is swept back from the face, but the bun, nape or net at the back cannot be '
                      'inspected. E = hair itself unclear. F = tied or pinned-up hair is clearly visible, but the mesh of the hairnet cannot be verified. Hair brushed behind ears is '
                      'insufficient to infer short hair.', 'ABCDEF','')
        status, detail = {'A':(OK,'可见头发用发网盘起。'), 'B':(ISSUE,'可见长发披散或马尾外露，未按要求用发网盘起。'),
                          'C':(OK,'可见颈后的短发发梢，短发免扎。'),
                          'D':(OK,'可见头发向后梳拢，露出部分未见披散；画面未显示发网细节。'),
                          'E':(MISSING,'头发被遮挡或发型细节看不清。'),
                          'F':(OK,'可见头发已束起或盘起，露出部分未见异常；画面未显示发网细节。')}[hair]
        findings.append(finding('扎发',status,detail))
    elif style == '扎发款':
        hair = choose([*hair_views, *pictures[:1]] if hair_views else pictures, 'Which describes the hair across these views? A = clearly tied back. B = clearly shorter than shoulders. '
                      'C = shoulder-length or longer hair clearly hanging loose. D = cannot tell.', 'ABCD', standard)
        status, detail = {'A':(OK,'可见头发已束起。'), 'B':(OK,'头发短于肩部，短发免扎。'),
                          'C':(ISSUE,'可见齐肩或更长的头发披散。'), 'D':(MISSING,'头发长度或束发状态看不清。')}[hair]
        findings.append(finding('扎发',status,detail))
    else:
        findings.append(finding('领带或扎发', MISSING, '缺少可区分工服款式的证据，需核实适用要求。'))
    return style, findings


def check_lower_body(paths, name, standard, body_context=(), roles=()):
    pictures, labels = [], []
    for index,path in enumerate(paths):
        with Image.open(path) as im:
            if min(im.size) < (2 if name == '皮鞋' else 32) or ImageStat.Stat(im.convert('L')).stddev[0] < 2:
                continue
            im.thumbnail((960,960))
            pictures.append(image_data(im))
            if roles:
                labels.append(roles[index])
    if name == '皮鞋':
        angles = '证据图顺序：'+'、'.join(labels)+'。' if labels else ''
        views = pictures or list(body_context)
        if not views:
            return finding(name, MISSING, '没有拍到可归属于举手者的鞋部画面。')
        answer = choose(views, angles+'Inspect only the exposed footwear of the marked person. A single toe, side '
                        'or outsole edge on one foot is enough. Use its visible shape, texture and sole profile '
                        'even when trousers hide the rest. A = exposed features look like a formal dress shoe; '
                        'B = athletic or casual shoe features such as a sneaker sole, mesh or canvas upper; '
                        'C = other visibly non-dress footwear such as an open sandal; '
                        'D = visible obvious dirt or damage on a dress shoe; '
                        'E = no identifiable piece of the subject’s footwear is visible. '
                        'Partial coverage alone is never E. Dark color alone is not evidence of leather.',
                        'ABCDE','')
        outcomes = {'A':(OK,'按露出部分的鞋型判断，外观与正装皮鞋相符。'),
                    'B':(ISSUE,'露出的鞋部特征更符合运动鞋或休闲鞋。'),
                    'C':(ISSUE,'露出的鞋部可辨认为其他鞋型，与正装皮鞋要求不符。'),
                    'D':(ISSUE,'皮鞋露出部分可见明显污损。'),
                    'E':(MISSING,'画面中没有可辨认并归属于举手者的鞋部特征。')}
    else:
        unknown = '下装被遮挡或剪裁、颜色看不清。'
        requirement = garment_clauses(standard, r'下装|裤|半裙|裙装|套裙|短裙|长裙')
        if not requirement:
            return finding(name,NO_STANDARD,'门店标准中未找到下装款式要求。')
        if not pictures and not body_context:
            return finding(name,MISSING,unknown)
        answer = choose([*pictures,*body_context], f'画面里下装款式是否符合“{requirement}”？'
                        '按颜色、长度和剪裁判断，宽腿或直筒都可能是西裤。'
                        'A 可见下装款式符合要求。B 明显不符或明显污损。C 下装被挡住或无法辨认款式。',
                        'ABC','')
        outcomes = {'A':(OK,'可辨认的下装款式符合门店要求：'+requirement+'。'),
                    'B':(ISSUE,'可见下装与门店要求不符或有明显污损。'),'C':(MISSING,unknown)}
    return finding(name,*outcomes[answer])
