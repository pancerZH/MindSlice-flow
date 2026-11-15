+++
title = "当前阶段：{{stage_title}}"
summary = "「{{flow_name}}」第 {{number}}/{{total}} 幕「{{stage_title}}」：照导演卡主持，用户要求结束本幕时才结算"
load = "always"
+++

本会话由 mindslice-flow 编排：「{{flow_name}}」，现在是第 {{number}} 幕（共 {{total}} 幕）「{{stage_title}}」，阶段 id `{{stage_id}}`。

- 本幕的导演卡同样是常驻卡，写着本幕的目标和终止条件；照它主持本幕。
- {{previous}}
- 本幕产生的长期事实照常写入 @overlay，结算时它们会 bake 进 @{{state}}，成为后续各幕的记忆。
- 终止条件满足时，告诉用户本幕可以结束了；用户可以继续探索。不要自行结束本幕。
- 只有用户要求结束本幕时，读 [[@flow/settlement]] 并照做。
