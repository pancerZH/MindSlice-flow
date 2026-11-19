+++
title = "结算本幕"
summary = "用户要求结束本幕时的步骤：整理 overlay、写本幕摘要、用户同意后运行 next 换幕"
+++

结算第 {{number}} 幕「{{stage_title}}」，按顺序做：

1. 运行 `{{flow_cmd}} status`，查看本幕留在 @overlay 的卡片和待审批变更。
2. 整理这些卡片：合并重复、修好 extends / supersedes 关系、把每条事实放到它所属主题的卡上。它们会全部 bake 进 @{{state}}。
3. 写本幕摘要卡 `summaries/{{stage_id}}`：发生了什么、留下的悬念、下一幕要承接的点。它会原样附在下一幕的常驻卡里，写得精炼些。没有这张卡，或 @overlay 的卡片有错误时，next 会拒绝。
4. 把结算摘要给用户看。有待审批变更时请用户决定；用户明确同意后才运行 `{{engine_cmd}} apply` 或 `{{engine_cmd}} discard`。
5. 用户同意结束本幕后，运行 `{{flow_cmd}} next`。它出错时照它的说明处理，不要绕过。
6. 向用户转述结果。{{after}}
