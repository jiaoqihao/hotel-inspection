# 开发与发布
main 保存可运行版本。按功能创建短期分支，例如 feat/phone-call-import 和 fix/camera-scheduler。提交说明写清行为变化。目录移动、数据迁移、业务变更分别提交。

## 开发约定
模块所有权见 [架构说明](docs/architecture/platform.md)。复用现有依赖，保持接口和数据的向后兼容。公共接口变更附旧记录回归样例。音视频、分机映射、通话明细、凭据、数据库及备份存于仓库外。提交前通过 git diff --cached 和 git check-ignore 核对。示例使用合成内容。

## 合并检查
从应用目录运行 npm ci、npm run check、npm run build。使用装有 local/requirements-test.txt 的 Python 运行：
```
python -m unittest local.test_patrol local.test_vision_hygiene local.test_speech local.test_review local.test_platform -v
```
共享音频变更同时验证胸牌与电话，媒体和账号变更验证三个模块；现场样本独立标注。UI 改动附脱敏截图。填写 PR 模板。

## 发布
通过验收的提交打版本标签，并记录版本差异及迁移版本。升级前停服务，备份整个运行目录并在隔离目录验证恢复。代码回退使用上一个发布标签，数据结构变化同时恢复匹配版本的数据库和媒体。
