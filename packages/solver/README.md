# PDE 与 FNO 模块预留

C 负责连续密度 / 速度场的 PDE 基准及 FNO 实验，当前没有求解器或模型。方程、边界离散、数值容差和依赖由 C 在 W1 调研并提出 ADR。

输出遵守联合评审后的 [数据契约](../../contracts/README.md)，同时提供不依赖 Web 的独立运行入口、基准检查和 [实验记录](../../docs/templates/experiment.md)。FNO 训练数据和权重不直接提交 Git。
