# 轨迹、聚合场与分析契约

版本 `0.2.0`，方向由用户确认，字段待 C/B/E 联调。旧 `0.1.0` 保留兼容；A 场景 `0.3` 是独立版本。

- C 输出 [trajectory.schema.json](trajectory.schema.json)：轨迹、稳定人员 ID 和退出事件。
- D 输出 [derived-field.schema.json](derived-field.schema.json)：密度和平均速度。
- D 独立输出 [analysis.schema.json](analysis.schema.json)：指标、序列和热点，供 B 使用。

## C 的输出

grid、walkable、exit_ids 必须与 A 最终栅格一致；scene_id 由上层分配并保持稳定，scene_revision 为实际修订，input_hash 取 A 的求解输入哈希。C 必须消费新掩码更新路径和碰撞，不能把单次模型调用当作完整场馆运行。

初版 population_mode=fixed_closed：全部人员在 t=0 出现，之后不新增/重入。每帧完整列出在场人员 agent_id、position_m、velocity_mps；米制坐标 x 右 y 上，数组 [iy][ix]，原点左下角。显式 time_s 严格递增。

exit_events 是每人唯一的 agent_id/exit_id/time_s，时间大于零且在记录区间内。事件发生在 t 时，该人不再出现在 t 帧。最后仍在场的人必须保留。消失无事件、越界、障碍内人员均报错。

initial_state_hash：人员按字符串 ID 排序，仅保留 ID、位置和速度；数值转 float，负零归零；UTF-8 JSON、ensure_ascii=False、sort_keys=True、紧凑分隔符、禁止 NaN，然后 SHA-256。可调用 D 的 initial_state_hash()。公平比较还要固定行为属性、种子、模型、规划、碰撞和时间配置；摘要不涵盖全部条件。

A 的 input_hash 仅用于同一仿真设置下的几何输入去重，跨模型/跨初始人员复用结果时不能单独作为缓存键，必须连同初始状态、模型和完整仿真设置核对。

source=stddn 要求实际 model_sha256、reproducibility_record 及 simulation_settings 的步长、种子、积分器、规划/碰撞版本。Mock 标记 mock，不能仅改标签作为真实结果。模型适用范围和运行环境记在实际运行记录中。

## D 的口径

位置向下取整到网格；密度=人数/(dx*dy)；速度=格内人员速度算术平均，空格零。初版整格可通行或不可通行，不平滑、不处理部分有效面积。

拥堵严格大于显式密度阈值。帧在 [t_i,t_(i+1)) 保持，累计量为 sum(area_i*dt_i)，单位 m2*s；末帧不外推。热点按格拆分连续超阈值区间，带 ix/iy、起止、观测持续时长和峰值；末帧首次观测的热点持续时长为零，不推断真实持续时间。

残留人数来自完整活动人员，出口累计人数来自事件。不输出未经定义的瞬时出口流率；未全离开时不能把模拟结束时间当作疏散完成时间。

## B 的读取

按场景、运行 ID、输入哈希与来源关联文件。指标/热点自带单位、来源、状态；序列自带 time_s，按 previous_sample 显示，不在观测范围外外推。真实人员回放来自 C，示意粒子另标明。来源 mock/stddn 不能写成旧 pde/fno。

当前分析成功对象 status=normal；无效数据 D 报错，不写零指标伪装成功。E/B 负责显示任务失败原因。待评价/非法/重复候选通过候选报告表达，与数值分析状态区分。

本轮未实现候选效果排名或比较结果文件，B 可先用 [分析样例](../examples/analysis.mock.json) 接指标/热点；真实排名需完整运行和公平条件后再扩展比较契约。
