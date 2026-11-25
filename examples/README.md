# 联调样例

此目录所有场结果均为 **Mock 合成数据**，不来自 PDE 或 FNO，不构成算法和优化验收证据。

- [scene.mock.json](scene.mock.json)：4×3 规则网格，含一个障碍、右边界出口及可调出口宽度范围。仅用于协议联调，不是最终体育馆模型。
- [field.mock.json](field.mock.json)：两帧任意密度 / 速度，用于验证读取、轴方向、热力和时间轴。帧间不满足质量守恒，不能用于推断真实出口流量。
- [task.mock.json](task.mock.json)：完成状态的任务快照，关联上述场景与结果；不是已经执行的求解任务。

样例通过草案契约并不意味着真实 PDE 输入表达已充分，也不替代完整场馆、几何验证或正式实验。

## 新轨迹路线的合成样例

- [trajectory.mock.json](trajectory.mock.json)：3 人、3 个不等间隔快照、2 个退出事件，手工构造，非物理仿真。
- [analysis.mock.json](analysis.mock.json)：生成的指标/热点，阈值 1.5 为手算假设，不是安全标准。
- [field-from-trajectory.mock.json](field-from-trajectory.mock.json)：D 聚合场，0.2.0，B 旧加载器需要适配。
- [layout-samples.mock.json](layout-samples.mock.json)：出口、通道、展台的 18 个组合，对应 [通道基准](../apps/web/scene-config/scenes/passage_optimization_example.json)，没有真实评价。
