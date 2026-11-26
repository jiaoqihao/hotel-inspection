"""Reviewed business operations shared by the web agent and the stdio adapter."""

from dataclasses import dataclass
from functools import lru_cache
import re

from local.patrol import Camera

# Explicit allowlist. Each entry is resolved against the live FastAPI schema.
ROUTES = """
GET /overview hotel_overview 查看门店概览
GET /reports store_reports 查看运营报表
GET /status system_status 查看本机模型与服务状态
GET /patrol patrol_overview 查看点位和巡检计划
POST /patrol/cameras camera_add 登记巡检点位
PUT /patrol/cameras/{key} camera_edit 修改点位标准和巡检计划
POST /patrol/cameras/{key}/{action} camera_control 暂停、恢复、立即巡检或移除点位
GET /vision/status vision_status 查看视觉模型和场景标准
GET /vision/jobs vision_list 查找巡检记录
POST /vision/jobs vision_import 导入选定的巡检素材
GET /vision/jobs/{job_id} vision_report 查看巡检结果和证据帧
GET /vision/jobs/{job_id}/frames/{name} vision_frame 打开巡检证据图
GET /vision/jobs/{job_id}/export vision_export 导出巡检报告
POST /vision/jobs/{job_id}/retest vision_retest 按当前标准复测
POST /vision/jobs/{job_id}/review vision_review 复核结果或登记整改
POST /vision/jobs/{job_id}/retry vision_retry 重试失败巡检
GET /badge/devices device_list 查看挂牌与胸牌设备及版本号
GET /badge/devices/presence device_presence 查看设备在线状态
GET /badge/devices/catalog device_catalog 查看厂商设备目录
POST /badge/devices device_register 登记设备
PATCH /badge/devices/{device_id} device_edit 更新设备档案、替换或停用设备
GET /badge/sync sync_status 查看设备同步设置
PUT /badge/sync sync_configure 更新设备平台连接
POST /badge/sync/run sync_start 开始同步录音
POST /badge/sync/pause sync_pause 暂停同步录音
GET /badge/record-index list_recordings 分页查询录音归档
GET /records recording_list 查看录音列表
POST /upload recording_import 导入选定的语音文件
GET /records/{id} recording_detail 查看录音、原话和报告
DELETE /records/{id} recording_delete 删除录音及关联资料
GET /records/{id}/audio recording_audio 打开录音回听
GET /records/{id}/export recording_export 导出录音报告
PATCH /records/{id}/review recording_review 更新录音复核意见
PATCH /records/{id}/tasks/{index} recording_task 更新改进待办
PATCH /records/{id}/speakers/{speaker_id} recording_speaker 标注说话人
POST /records/{id}/retranscribe recording_retranscribe 创建重新转写版本
PATCH /records/{id}/metadata recording_metadata 修改录音档案信息
POST /records/{id}/deepseek-copy recording_reprocess 用历史转写生成新版报告
POST /records/{id}/analyze recording_analyze 分析已有录音
GET /badge/customers customer_list 查看客户档案
POST /badge/customers customer_create 新建客户档案
PATCH /records/{id}/customer customer_link 关联录音和客户
PATCH /records/{id}/findings/{group}/{index} finding_review 复核报告中的结论
GET /badge/wecom/settings wecom_settings 查看企微连接及定时推送设置
PUT /badge/wecom/settings wecom_configure 更新企微连接及定时推送设置
POST /badge/wecom/test wecom_test 发送企微连接测试
GET /badge/wecom/preview wecom_preview 查看指定日期日报与话术达成情况
POST /badge/wecom/message-preview wecom_message_preview 预览日报发送内容
POST /badge/wecom/prepare wecom_prepare 准备日报
POST /badge/wecom/send wecom_send 向管理群发送日报
GET /badge/wecom/history wecom_history 查看推送历史
GET /badge/wecom/history/{run} wecom_history_detail 查看已归档推送内容
POST /badge/wecom/history/{run} wecom_resolve 核对推送状态或重试投递
GET /badge/wecom/bindings/{device} wearer_list 查看设备佩戴人历史
PUT /badge/wecom/bindings/{device} wearer_update 更新设备佩戴关系
GET /badge/wecom/rules script_rules 查看适用话术标准
POST /badge/wecom/rules script_update 更新话术标准
POST /badge/wecom/reviews/{record_id} script_review 复核员工话术
GET /phone/extensions phone_extensions 查看分机映射
PUT /phone/extensions phone_extension_edit 修改分机映射
POST /phone/calls phone_import 导入选定的电话录音
GET /phone/calls phone_list 查询通话记录
POST /phone/reconcile phone_reconcile 导入话单并对账
GET /phone/health phone_health 查看接入状态和对账结果
GET /phone/calls/{key} phone_detail 查看通话、原话和客需
GET /phone/calls/{key}/export phone_export 导出通话报告
GET /phone/calls/{key}/audio phone_audio 回听电话录音
POST /phone/calls/{key}/retry phone_retry 重试失败通话
POST /phone/calls/{key}/items phone_add_item 创建客需事项
POST /phone/items/{item}/dispatch phone_dispatch 将已核实客需派单
POST /phone/items/{item}/dismiss phone_dismiss 忽略客需事项
GET /phone/orders order_list 查询工单、处理记录及状态
POST /phone/orders/{oid} order_transition 接单、完成或复核工单
POST /phone/orders/{oid}/follow order_follow 跟进工单与交班
GET /phone/duty duty_overview 查看电话值班
PUT /phone/duty duty_configure 更新值班配置
POST /phone/duty/{key} duty_handle 处理值班提醒
GET /phone/ledger phone_ledger 查看接入台账
POST /phone/ledger/{row_id} ledger_review 复核台账差异
GET /phone/items/{item}/suggestions order_suggestions 查看可关联工单
GET /phone/register phone_register 查询电话工作台记录
POST /phone/calls/{key}/review phone_review 人工复核通话分析
POST /phone/calls/{key}/reanalyze phone_reanalyze 重新分析通话
GET /phone/calls/{key}/versions/{version} phone_version 查看历史通话分析
POST /phone/calls/{key}/read phone_read 标记预警已读
PUT /phone/calls/{key}/favorite phone_favorite 更新通话收藏
POST /phone/items/{item}/link order_link 将客需关联到工单
GET /phone/reports phone_report 查询电话运营分析
GET /phone/reports/export phone_report_export 导出电话运营指标
""".strip()


def obj(properties=None, required=()):
    return dict(
        type="object",
        properties=properties or {},
        required=list(required),
        additionalProperties=False,
    )


STR = {"type": "string"}
INT = {"type": "integer"}
OFFSET = dict(type="integer", minimum=0, default=0)
LIMIT = dict(type="integer", minimum=1, maximum=100, default=20)
CUSTOM = {
    "batch_update": (
        "批量办理多个写操作，先展示目标清单并一次确认",
        obj(
            {
                "actions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 60,
                    "items": obj(
                        {"name": STR, "arguments": {"type": "object"}},
                        ["name", "arguments"],
                    ),
                }
            },
            ["actions"],
        ),
    ),
    "order_detail": ("查看一张工单及其处理记录", obj({"oid": INT}, ["oid"])),
    "vision_export_json": ("导出巡检报告 JSON", obj({"job_id": STR}, ["job_id"])),
    "recording_export_html": ("下载可阅读的录音 HTML 报告", obj({"id": STR}, ["id"])),
    "business_day": (
        "按北京时间业务发生日查询正式或测试样本，返回总数和分页索引",
        obj(
            {
                "module": {"enum": ["摄像头", "语音", "电话"], "type": "string"},
                "day": STR,
                "test": {"type": "boolean"},
                "offset": OFFSET,
                "limit": LIMIT,
            },
            ["module", "day"],
        ),
    ),
    "device_position": (
        "只修改指定设备的岗位，先查询设备编号及版本",
        obj(
            {
                "number": STR,
                "revision": INT,
                "position": dict(type="string", minLength=1, maxLength=80),
            },
            ["number", "revision", "position"],
        ),
    ),
    "recording_module": (
        "按录音编号查看场景识别、分页原话或录音质量",
        obj(
            {
                "recording_id": STR,
                "module": {
                    "type": "string",
                    "enum": ["场景识别", "原话撰写", "录音质量"],
                },
                "offset": OFFSET,
                "limit": dict(LIMIT, default=30),
            },
            ["recording_id", "module"],
        ),
    ),
}


@dataclass
class Tool:
    name: str
    label: str
    schema: dict
    method: str = "GET"
    path: str = ""
    form: bool = False

    def public(self):
        return {
            "name": self.name,
            "description": self.label,
            "inputSchema": self.schema,
            "writing": self.method != "GET",
        }


def inline(node, definitions):
    if isinstance(node, list):
        return [inline(value, definitions) for value in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return inline(definitions[node["$ref"].rsplit("/", 1)[1]], definitions)
    return {k: inline(v, definitions) for k, v in node.items() if k != "title"}


@lru_cache(maxsize=2)
def catalog(app):
    spec = app.openapi()
    tools = {
        name: Tool(
            name,
            label,
            schema,
            "PATCH" if name in {"device_position", "batch_update"} else "GET",
        )
        for name, (label, schema) in CUSTOM.items()
    }
    for line in ROUTES.splitlines():
        method, suffix, name, label = line.split(maxsplit=3)
        path = "/api" + suffix
        op = spec["paths"][path][method.lower()]
        props, required = {}, []
        for param in op.get("parameters", []):
            props[param["name"]] = param["schema"]
            if param.get("required"):
                required.append(param["name"])
        body = op.get("requestBody", {}).get("content", {})
        form = "multipart/form-data" in body
        if body:
            props["body"] = next(iter(body.values()))["schema"]
            required.append("body")
        elif name in ("camera_add", "camera_edit"):
            camera = Camera.model_json_schema()
            props["body"] = (
                {"type": "array", "items": camera} if name == "camera_add" else camera
            )
            required.append("body")
        if name == "camera_control":
            props["action"] = {
                "type": "string",
                "enum": ["pause", "resume", "now", "remove"],
            }
        schema = inline(obj(props, required), spec["components"]["schemas"])
        if name == "recording_list":
            schema = obj({"query": STR, "offset": OFFSET, "limit": LIMIT})
        if form:
            schema["properties"]["body"]["properties"]["file"] = dict(
                STR, description="用户选择并上传的本地附件编号"
            )
        tools[name] = Tool(name, label, schema, method, path, form)
    return tools


def needs_confirmation(name, args):
    body = args.get("body") or {}
    return (
        name
        in {
            "batch_update",
            "recording_delete",
            "wecom_send",
            "wecom_test",
            "wecom_resolve",
            "wecom_configure",
            "wecom_prepare",
            "sync_configure",
            "sync_start",
            "script_update",
        }
        or name == "phone_review"
        and len(body.get("segments", [])) > 1
        or name == "camera_control"
        and args.get("action") == "remove"
        or name == "device_edit"
        and (body.get("retired") or body.get("replacement"))
        or name == "camera_add"
        and len(body) > 1
        or name == "device_register"
        and len(body.get("devices", [])) > 1
    )


def resolved_path(tool, args):
    def segment(m):
        value = str(args.get(m[1], ""))
        if not re.fullmatch(r"[\w.\-]{1,160}", value) or value in (".", ".."):
            raise ValueError("记录编号或路径参数无效")
        return value

    return re.sub(r"\{(\w+)\}", segment, tool.path)
