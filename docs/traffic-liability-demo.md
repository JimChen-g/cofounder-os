# 交通事故判责预测 Demo

本 Demo 在 D06-D13 已验收契约上增加一个完全合成、可重复录屏的行业案例。它不改变 Gateway、Workflow Controller、Agent、Artifact、Policy、Approval、Audit 或 Evaluation 契约。

## 当前边界

- 案例不包含真实姓名、车牌、地点或事故材料。
- 页面中的 80% / 20% 和 86% 置信度是固定演示结果。
- 路由记录明确标识为 `qwen-traffic-liability-demo-fixture-v0`。
- 本次不调用实时模型；正式 Adapter 状态是 `pending_clean_dataset_and_formal_adapter`。
- 所有主要产物都声明“非执法、非司法、非理赔结论”，最终结果受 Founder 人工审批控制。

## 一次性启动

在项目根目录分别执行以下命令：

```bash
source .venv/bin/activate
python scripts/seed_traffic_liability_demo.py --force-new
PRODUCT_DATA_DIR=/tmp/cofounder-os-traffic-demo/data GATEWAY_PORT=9100 bash scripts/run_gateway.sh
```

浏览器打开：

```text
http://127.0.0.1:9100/ui
```

新浏览器不会保存上一次选中的 Run。如果首次打开看到任务输入框，先进入
**Evaluation**，在唯一一条事故 Demo 记录上点击 **Inspect Run**；页面会自动
返回已载入数据的 **Mission**，然后再开始录屏。

如果端口 9100 已被占用，可换成其他端口，并同步修改浏览器地址。重复运行播种脚本默认复用最新同案例 Run；加 `--force-new` 会生成一个新的待审批 Run，不会删除旧数据。

## 90 秒 Demo 视频路径

1. **Mission（0-20 秒）**：说明合成案例和非执法边界；展示三个 Agent 完成、九个产物完整、Qwen 演示路由以及 Founder 审批待决。
2. **Artifacts（20-45 秒）**：打开 `executive-decision-memo.md`，展示车辆 B 主要责任 80%、车辆 A 次要责任 20%、86% 置信度、证据链和缺失证据。
3. **Approvals（45-65 秒）**：展示两条策略规则；Reviewer 保持 `founder`，填写“已核对合成证据与非权威边界，同意用于 Demo 展示”，点击 **Approve & resume**。
4. **Mission（65-75 秒）**：展示 Workflow Controller 将状态收口为 Completed。
5. **Audit trail / Evaluation（75-90 秒）**：展示路由、产物校验、审批事件和可解释评分。

## 替换为正式模型的入口

数据清洗和 Qwen Adapter 完成后，保留相同的 Run/Task/Artifact 契约，只替换推理来源：

1. 用真实、合规、已脱敏的输入适配器替换 `examples/traffic-liability-demo-case.json`。
2. 将 `deterministic_demo_fixture` 切换为正式推理模式，并记录 Adapter 版本、数据版本和模型哈希。
3. 将模型输出映射到相同的九个 Artifact 名称。
4. 保留人工复核、缺失证据、免责声明、审计与评估步骤。

这样正式案例上线时不需要重构 D06-D13。
