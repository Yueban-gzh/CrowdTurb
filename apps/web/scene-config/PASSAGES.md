# 通道宽度扩展

D 在 A 库基础上新增场景 0.3，兼容旧 0.1/0.2。当前是 Python/JSON 支持；原网页编辑器仍拒绝 0.3，不能改版本号绕过限制。

geometry.passages 每个对象有 id/name/center/direction/length/width/envelope_width/width_limits/adjustable。x/y 为通行轴向，净宽垂直于轴，中心和长度固定。包络中通道两侧是实体隔挡，随净宽对称改变。

不清除固定墙或障碍；包络不能越界、相互重叠、穿越固定结构；隔挡不能压人群、出口或设施。继续验证连通和近似最小通路宽度。只支持轴对齐，不是精确工程净空。

候选为 {"passage_id":"P1","width":6.0}，只允许改宽度，受权限、width_limits 和声明范围约束。调用 apply_layout()/evaluate_candidates()，不绕过检查直接改 JSON。

报告增加 changed_passages/passages/passage_width_deltas，记录前后宽度与净面积变化，这是修改量而非实际造价。NPZ 增加 passage_wall_mask/passages_json；隔挡计入 obstacle_mask/walkable/input_hash。C 使用最终掩码更新路径与碰撞，D 按 input_hash 去重。

本契约以最终掩码为计算几何；若 C 改用连续几何碰撞，必须共同更新哈希口径，避免错误去重。

例子：[通道基准](scenes/passage_optimization_example.json)、[Schema](schema/scene_schema_v0.3.json)。基准为独立合成案例，不改 A 原体育馆，无真实仿真结论。B 后续需画出通道和两侧隔挡。
