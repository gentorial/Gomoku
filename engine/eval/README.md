# 评估器扩展

公开接口位于 include/gomoku/evaluator.h。
每次搜索先 reset(root)，每次走子后 push(position, move)，撤销时 pop()。
搜索中断也必须恢复这对操作；已有测试用跟踪评估器验证栈平衡。

handcrafted-v1 是可运行的基线，不加载神经网络。
`NnueModel` 严格加载 [正式格式](../../models/line11-format.md)，不可变权重在会话间共享。
`NnueEvaluator` 提供价值及 `move_scores` policy 排序；同时缓存黑白两种绝对视角，
每次只重算落子周围四条半径 5 的棋形以及空间卷积覆盖范围，维护全局/区域和。
撤销恢复局部缓存与汇总，终止搜索后仍可继续使用同一评估器。
全量推理和增量更新属于同一个模型，全量实现长期保留为校验参考；
必须测试走子、撤销、搜索取消与从完整局面重建的等价性。

不要为每次 evaluate 调用 Python、HTTP 或 JSON。
权重解析/兼容性检查在搜索前完成，不在节点循环中完成。
