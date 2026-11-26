"""Inspect one gesture target. Ask the local VLM one observable question at a time."""
from collections import Counter
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


def jacket_color(pictures, panels, descriptions):
    # Extract only outerwear clauses. A black trouser or sock requirement cannot set jacket color.
    clauses = [garment_clauses(d, r'外套|上衣|西装(?!裤)|西服(?!裤)') for d in descriptions]
    colors = [set(re.findall(r'黑|蓝|青|灰|白|红|棕|褐|绿|紫|黄|米色|咖啡', c)) for c in clauses]
    if any(c != colors[0] for c in colors):
        return finding('工服', MISSING, '男士和女士的外套配色要求不同，当前画面不足以确定适用要求。')
    if not colors[0]:
        return finding('工服', OK, '可见已穿西装外套。')
    panel = panels[0]
    rgb = ImageStat.Stat(panel).median
    views = [pictures[0], image_data(panel.resize((128,128), Image.Resampling.LANCZOS))]
    context = f'只看举手人的西装外套，排除白衬衫、领带和背景。胸前面料区域的RGB中位数是{rgb}，结合原图核实。'
    if colors[0] == {'黑'}:
        # Navy and black can look similar in a dim frame. Corroborate a blue
        # classification on the whole garment with blue dominance on its fabric.
        red, green, blue = rgb
        if blue - max(red, green) > 16 and blue > 1.25 * max(red, green):
            hue = choose([pictures[0]], 'What is the visible color of the suit jacket fabric, ignoring tie, '
                         'shirt and background? A = black or neutral near-black. B = blue or navy blue, '
                         'including dark blue. C = another visible color. D = lighting or occlusion prevents '
                         'distinguishing the color.', 'ABCD','')
            if hue == 'B':
                return finding('工服', ISSUE, '可见西装外套呈蓝色，与要求的黑色不符。')
        color = choose(views, context+'暗部轻微偏色仍可为黑；清楚呈蓝色的西装与黑色有别。观察到的外套颜色是什么？'
                       'A 黑色或视觉上接近中性的黑色。B 明显蓝色或深蓝。C 其他明显非黑的颜色。D 光照或遮挡无法辨色。',
                       'ABCD','')
        if color == 'B':
            return finding('工服', ISSUE, '可见西装外套呈蓝色，与要求的黑色不符。')
        if color == 'C':
            return finding('工服', ISSUE, '可见西装外套颜色与要求的黑色不符。')
        if color == 'A':
            return finding('工服', OK, '可见已穿黑色西装外套。')
    else:
        color = choose(views, context+f'只比较外套颜色与“{clauses[0]}”的颜色要求。'
                       'A 颜色相符；B 颜色明确不符；C 光照或遮挡导致无法辨色。','ABC','')
        if color == 'B':
            return finding('工服', ISSUE, '可见西装外套颜色不符合点位配置。')
        if color == 'A':
            return finding('工服', OK, '可见已穿西装外套，颜色符合点位要求。')
    return finding('工服', MISSING, '可见西装外套，但画面不足以核实面料颜色。')


def check_person(paths, standard, body_context=(), hair_views=(), *, jacket_styles=('','')):
    portraits, panels = [], []
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
            portraits.append(picture)
            w,h = im.size
            if exposed(im,(.33,.42,.65,.75)):
                pictures.append(picture)
                panels.append(im.convert('RGB').crop((round(.62*w),round(.44*h),round(.79*w),round(.70*h))))
            if exposed(im,(.50,.43,.78,.68)):
                chest = im.crop((round(.46*w),round(.32*h),round(.95*w),round(.75*h)))
                closeup = image_data(chest.resize((chest.width*3,chest.height*3), Image.Resampling.LANCZOS))
                if visible_badge_area(picture):
                    badge_views.append((closeup,picture))
                else:
                    partial_chest.append((closeup,picture))

    front_views = portraits or list(body_context[:1])
    rule = choose(front_views, '只看举手者正面头部和上身，判断本次适用的前台仪容规范。A 男士，检查领带；B 女士，检查扎发；C 人物不清楚，无法可靠区分。领带、发型和西装款式不作为选择规范的依据。', 'ABC', '') if front_views else 'C'
    style = {'A':'男士规范', 'B':'女士规范', 'C':'适用规范待核实'}[rule]
    applicable = list(jacket_styles)
    if rule in ('A','B'):
        applicable = [jacket_styles[0 if rule == 'A' else 1]]
    uniform = finding('工服', MISSING, '上身被遮挡、过暗或外套剪裁看不清。')
    if pictures:
        cut = choose(pictures, 'Inspect only the outer garment. A = clearly not a suit blazer: casual jacket, '
                     'sportswear, or just a shirt or sweater without a suit jacket. B = visibly wearing a suit '
                     'blazer, including open or unbuttoned blazers. Use any visible lapels, front panels or sleeves. C = no usable outerwear features visible. '
                     'Ignore the color and style of whatever is underneath.', 'ABC','')
        if cut == 'A':
            uniform = finding('工服', ISSUE, '上身衣着清楚可见，未穿西装外套。')
        elif cut == 'B':
            uniform = jacket_color(pictures, panels, [d or standard for d in applicable])

    badge_prompt = ('Inspect this crop of the left upper chest, ignoring the shirt and tie. What discrete object '
                    'is attached to the jacket? A = a flat rectangular employee nameplate with a broad face and '
                    'distinct straight sides, wider than it is tall. B = plain cloth, seams, pocket, a tiny dot or round lapel pin only. '
                    'C = the chest area is obscured or too blurry to inspect.')
    badge = choose([v[0] for v in badge_views], badge_prompt, 'ABC','') if badge_views else 'C'
    if badge == 'C' and partial_chest:
        visible = choose([v[0] for v in partial_chest], badge_prompt, 'ABC','')
        if visible == 'A':
            badge, badge_views = 'A', partial_chest
    if badge == 'A':
        placement = choose([v[1] for v in badge_views], '检查胸前名牌是否妥帖。A=佩戴在本人左上胸且固定平整；'
                           'B=明显歪斜、松脱或错位；C=角度或画质不足以判断。', 'ABC', standard)
        status, detail = {'A':(OK,'本人左上胸可见长方形工牌，佩戴端正。'), 'B':(ISSUE,'胸牌明显歪斜、松脱或位置不当。'),
                          'C':(MISSING,'可见长方形工牌，佩戴位置或固定状态看不清。')}[placement]
        badge_rule = garment_clauses('。'.join([standard,*applicable]), r'工牌|工号牌|胸牌|名牌')
        if status == OK and '金色' in badge_rule:
            finish = choose([v[0] for v in badge_views], 'Look only at the rectangular nameplate on the jacket. '
                            'A = gold or warm brass-colored face; B = clearly a different color; '
                            'C = the color is obscured.', 'ABC','')
            if finish == 'B':
                status, detail = ISSUE, '本人左上胸的长方形工牌颜色与金色要求不符。'
            elif finish == 'C':
                status, detail = MISSING, '可见长方形工牌，画面不足以核实规定的金色。'
    elif badge == 'B':
        status, detail = ISSUE, '本人左上胸佩戴区域可见，未见带直边和牌面的长方形工牌。'
    else:
        status, detail = MISSING, '胸前佩戴区域被遮挡或不清晰。'
    findings = [uniform, finding('胸牌',status,detail)]
    if style == '男士规范':
        tie = choose(pictures or front_views, '检查衬衫领口。A=佩戴领带且领结在领口中央；B=清楚看到空领口，未戴领带；'
                     'C=领结明显歪斜或松脱；D=遮挡或细节看不清。', 'ABCD', standard)
        status, detail = {'A':(OK,'领口可见领带，领结居中。'), 'B':(ISSUE,'领口清楚可见，未戴领带。'),
                          'C':(ISSUE,'领结明显歪斜或松脱。'), 'D':(MISSING,'领口或领结细节看不清。')}[tie]
        findings.append(finding('领带',status,detail))
    elif style == '女士规范' and '发网' in standard:
        hair = choose([*hair_views, *front_views[:1]], 'Inspect only visible hair across the views of the same person. A = a bun enclosed in a visibly identifiable hairnet. '
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
    elif style == '女士规范':
        hair = choose([*hair_views, *front_views[:1]], 'Which describes the hair across these views? A = clearly tied back. B = clearly shorter than shoulders. '
                      'C = shoulder-length or longer hair clearly hanging loose. D = cannot tell.', 'ABCD', standard)
        status, detail = {'A':(OK,'可见头发已束起。'), 'B':(OK,'头发短于肩部，短发免扎。'),
                          'C':(ISSUE,'可见齐肩或更长的头发披散。'), 'D':(MISSING,'头发长度或束发状态看不清。')}[hair]
        findings.append(finding('扎发',status,detail))
    else:
        findings.append(finding('领带或扎发', MISSING, '正面人物画面不足以可靠区分适用的男士或女士规范，请人工核实。'))
    return style, findings


def check_shoes(views, roles):
    # The model can mistake carpet texture or a highlight in one crop for a trainer.
    # Inspect each view on its own and give each camera angle one vote.
    by_angle = {}
    visible = []
    for i, view in enumerate(views):
        angle = roles[i] if roles else str(i)
        if angle in ('鞋履细节', '放手后鞋履', '正面鞋履'):
            angle = '正面'
        choice = choose([view], 'Which best describes the visible shoe construction? '
                        'A = broadly leather-like footwear including business-casual lace-ups, slip-ons, loafers or dress shoes. '
                        'B = clearly a running shoe, canvas trainer or another distinctly non-leather shoe. Laces and rubber edges alone are inconclusive. '
                        'C = sandal, slipper or open footwear. D = part of a shoe is visible but its construction is unclear. '
                        'E = no identifiable part of a shoe is visible.', 'ABCDE', '')
        if choice == 'E':
            continue
        visible.append(view)
        if choice != 'D':
            by_angle.setdefault(angle, set()).add(choice)
    if not visible:
        return finding('皮鞋', MISSING, '画面中没有可辨认并归属于举手者的鞋部特征。')
    votes = Counter(next(iter(choices)) for choices in by_angle.values() if len(choices) == 1)
    choice = None
    if votes:
        best, count = votes.most_common(1)[0]
        if count * 2 > sum(votes.values()):
            choice = best
    if choice is None and 'C' not in votes:
        sole = choose(visible, "Inspect the sole profile of this person's shoes in the side views. "
                      "A low-profile dress outsole with a narrow edge and low heel; B distinctly built-up athletic "
                      "cushioning or a canvas sneaker's rubber toe cap and foxing; C outsole profile unclear.",
                      'ABC', '')
        choice = {'A':'A', 'B':'B', 'C':None}[sole]
    if choice == 'B':
        return finding('皮鞋', ISSUE, '可见明显运动鞋或其他非皮鞋结构。')
    if choice == 'C':
        return finding('皮鞋', ISSUE, '露出部分可辨认为凉鞋或拖鞋。')
    if choice is None:
        return finding('皮鞋', MISSING, '可用画面未能一致辨认鞋型或鞋底特征。')
    condition = choose(visible, 'Inspect only the exposed shoes across views. A = clearly visible dirt or stains '
                       'on the upper itself, distinct from shine, shadow, outsole edges and carpet. '
                       'B = clearly light or brightly colored shoes. C = broadly dark shoes with no obvious stain '
                       '(black, dark brown or dark navy are acceptable). D = shoe color cannot be seen.',
                       'ABCD', '')
    if condition == 'A':
        return finding('皮鞋', ISSUE, '露出的鞋面上可见明显污渍。')
    if condition == 'B':
        return finding('皮鞋', ISSUE, '可见鞋面呈浅色或亮色，与深色皮鞋要求不符。')
    if condition == 'D':
        return finding('皮鞋', MISSING, '鞋面颜色没有可辨认的画面。')
    return finding('皮鞋', OK, '露出部分符合深色皮鞋外观，未见明显污渍。')


def check_lower_body(paths, name, standard, body_context=(), roles=()):
    pictures, labels = [], []
    for index,path in enumerate(paths):
        with Image.open(path) as im:
            if min(im.size) < (2 if name == '皮鞋' else 32) or ImageStat.Stat(im.convert('L')).stddev[0] < 2:
                continue
            if name == '皮鞋' and im.format == 'JPEG' and max(im.size) <= 960:
                pictures.append(encoded_image(path))
            else:
                im.thumbnail((960,960))
                pictures.append(image_data(im))
            if roles:
                labels.append(roles[index])
    if name == '皮鞋':
        views = pictures or list(body_context)
        if not views:
            return finding(name, MISSING, '没有拍到可归属于举手者的鞋部画面。')
        return check_shoes(views, labels)
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
