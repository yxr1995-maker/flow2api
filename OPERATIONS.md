# Flow2API 运维手册

仓库: 本仓库（flow2api，脚本目录即仓库根，下文以 $REPO 指代）｜ 服务名: com.flow2api.server ｜ 端口: 8000

## 1. 恢复（服务未加载时）

以当前 macOS 用户执行加载并拉起（无需 sudo）:

    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.flow2api.server.plist
    launchctl kickstart -k gui/$(id -u)/com.flow2api.server

模板 com.flow2api.server.plist.template 使用 __REPO_ROOT__ 占位符:安装前 sed 替换为实际仓库路径,ThrottleInterval=10,Umask 保留 63.

## 2. 重启

    $REPO/stop.sh
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.flow2api.server.plist
    launchctl kickstart -k gui/$(id -u)/com.flow2api.server

不要按端口 kill 进程,不要擅自重启 ComfyUI.

## 3. 验证

    $REPO/doctor.sh
    curl -s -m 5 http://127.0.0.1:8000/health

通过标准:doctor.sh 全 PASS,health 返回 200 且 backend_running 为 true.
token 为 0 时属账号侧状态,不代表服务故障.

## 4. 日志

    tail -n 30 $HOME/Library/Logs/flow2api/server.log
    tail -n 30 $HOME/Library/Logs/flow2api/server.err.log

仓库内 server.log 为历史遗留,不再写入.
