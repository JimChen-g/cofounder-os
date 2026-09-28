# 本机 Cofounder 门户

此目录是正式可复现来源。`static/` 提供中文工程门户；`/ui` 与其资源直接读取仓库中的 `app/ui/static/`。不再对远端 HTML 或 JavaScript 做字符串替换。共享状态函数也从同一仓库读取。

## 启动与停止

在安装了 `paramiko` 的 Python 环境中执行（将占位符替换为本机私人路径，路径和秘密不要提交）：

```sh
python tools/local_portal/launch.py \
  --port 19000 --ssh-host HOST --ssh-port SSH_PORT --ssh-user USER \
  --ssh-key /private/path/ssh_key --known-hosts /private/path/known_hosts \
  --token-file /private/path/founder.token \
  --remote-root /home/Developer/cofounder-t07-t10 \
  --state /private/path/portal-state.json --log /private/path/portal.log
```

也可使用相同参数直接运行 `server.py`（去掉 `--log`），在前台启动。`launch.py` 使用当前 Python 解释器并让服务脱离终端。打开 `http://127.0.0.1:19000/`。停止时核实 state 文件中的 PID 对应本门户服务，再向该 PID 发送 TERM；这不会停止 Spark 产品 API。重新部署本地源码后需先停止既有门户，否则启动器会返回 already_running；请核对启动日志和页面来源。

SSH 私钥、known_hosts、服务令牌、状态、日志均位于 Git 之外。令牌文件必须仅本机用户可读。服务凭据只由代理持有，不进入页面、URL、浏览器脚本。HTTP 只绑定 loopback。入口保留 Host、Origin、本机会话、owner、路径白名单、版本绑定和写入互斥保护；SSH 主机密钥必须预先登记。

## 读状态与不确定写结果

`/local/runs` 返回当前 owner 最近 200 条已持久化记录，包括 `request_id`、原始状态、delivery 状态、版本、截止时间和失败原因。UI 用共享纯函数派生显示状态，不修改历史。`/local/status`、列表与评测读取共享两秒缓存，避免同次刷新重复启动 SSH Python；任何已转发写请求都会使缓存失效，包括响应未知时。

评测按 inventory 补充事实。工程记录保留在历史中，通用工作流分数为 null，且不计入通用分数统计；测试和模型复核证据仍由候选详情提供。没有新产品 API。远端 inventory 目前仍使用受控 SSH 只读查询，迁移为产品列表 API 留待后续。

POST 从不自动重放。工程创建响应未知时，UI 可按 inventory 的 request_id 查找；工程操作响应未知时，只读检查已有 snapshot 的 delivery.receipts 或 cancel_receipts。没有读到回执不能推断操作失败，更不能自动重试。私人运行数据不随源码提交。

## 离线回归

```sh
python -B -m unittest discover -s tools/local_portal -p test_server.py -v
```

测试通过内存传输运行真实 HTTP handler，fake Spark 不连远端、不读凭据、不代用户批准任何真实候选。覆盖 owner/会话/来源/版本/路径边界、超时不重放、工程评分排除、仓库文件原样服务和 inventory 缓存失效。
