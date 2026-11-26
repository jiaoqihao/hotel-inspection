# 单店服务器发布

GitHub Actions 对指向 main 的 PR 做类型检查、生产构建和隔离数据回归。合并到 main 后再次验证，并通过受限 SSH 密钥传送同一次构建生成的网页和 FastAPI 服务。可在 Actions 的 Verify and deploy 页面选择 main 重跑。部署目标、主机公钥和专用私钥分别配置在仓库 Secrets 的 QIJIAN_TARGET、QIJIAN_KNOWN_HOSTS、QIJIAN_DEPLOY_KEY 中。

Ubuntu 24.04 主机使用 Python 3.12、系统 pip、venv、rsync、flock 与用户级 systemd。安装 ops/qijian.service 到部署用户的 ~/.config/systemd/user，启用用户 linger 与该服务。将 ops/deploy.sh 安装到 ~/qijian/deploy.sh 并赋予执行权限。部署公钥在 authorized_keys 中使用 restrict 和指向该脚本的固定 command。服务器校验 40 位提交 SHA，串行解包到独立目录，按依赖文件散列复用虚拟环境。

~/qijian/config/service.env 权限为 0600。设置 LOCALAPPDATA、HOTEL_VOICE_DATA、HOTEL_STORE_ORIGIN、HOTEL_LLAMA_SERVER、HOTEL_CREDENTIAL_KEY 和 DEEPSEEK_API_KEY。ORIGIN 为无尾斜杠的完整 HTTPS 地址，与 Caddy 独立站点名一致，反向代理至 127.0.0.1:8765。Linux 凭据密钥用 Fernet.generate_key() 生成，独立安全备份。迁移 Windows 的 DPAPI 加密记录时需重新配置对应的设备凭据。

共用域名时可将应用挂载到独立目录。外部入口使用尾斜杠，例如 /qijian/。Caddy 仅向后端转发该目录的请求，并去除目录前缀。将 HOTEL_STORE_ORIGIN 设为浏览器的 HTTPS 源地址，HOTEL_STORE_COOKIE_PATH 设为 /qijian/，使登录会话仅随该路径发送。

运行数据放在 ~/qijian/data，模型权重放在其 models 子目录，Linux CPU 运行库独立安装。安装并启用 ops/qijian-text.service 以运行回环端口 8766 上的电话推理与胸牌脱敏模型。视觉模型按需启动，两者空闲 30 秒后释放模型内存。可通过 ~/qijian/config/pip.conf 指定主机可达的 PyPI 镜像。创建或重置门店账号时，进入 ~/qijian/current，执行以下命令，在终端输入密码。

```bash
set -a
. ~/qijian/config/service.env
set +a
venv/bin/python -m local.access manager 管理员
```

每次发布停服务后将业务数据备份至 ~/qijian/backups 并对 SQLite 副本执行完整性检查。模型单独保管。切换 current 后检查首页、健康接口、门店登录模式和匿名读取权限。检查失败时恢复对应的数据和上一版代码。生产服务日志由 journalctl --user -u qijian.service 提供，线上提交记录在 ~/qijian/current/REVISION。定期将已核验的备份转存到受限的外部存储并检查剩余空间。

人工回退前停服务，在隔离目录中还原目标版本的备份并核对数据及历史媒体，保留当前运行目录副本，再切换至配套的 releases 目录和已核验的数据。部署脚本或用户级单元本身有变更时同步更新服务器上的安装副本。
