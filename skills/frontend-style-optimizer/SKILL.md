---
name: frontend-style-optimizer
description: 前端风格优化技能：规范一致性、可读性、性能与可维护性改进。
---

# Frontend Style Optimizer Skill (Progressive)

## 触发场景
- 用户请求优化前端代码风格或修复 lint/format 问题
- React/Vue/TS/JS/CSS 多文件改动需要统一规范
- 希望在不破坏行为的前提下提升代码质量

## 快速工作流
1) 识别技术栈与规范来源（eslint/prettier/stylelint）。
2) 先做安全改动：格式与低风险重构。
3) 再做结构优化：命名、拆分、复用、可维护性。
4) 最后验证：构建、lint、关键交互回归。

## Progressive Disclosure
### Layer 1（默认）
- 给最小风格修复方案与影响评估。
- 列出必须修复的规则冲突。

### Layer 2（按需展开）
- 对指定文件输出更细的重构建议与示例片段。
- 提供规则级别配置建议（仅必要项）。

### Layer 3（深度模式）
- 给项目级规范治理方案（规则、目录、review checklist、落地步骤）。

## 优化清单
- 规范一致性：eslint/prettier/stylelint 冲突与统一。
- 可读性：命名、函数体长度、组件职责、注释质量。
- 性能：不必要重渲染、无效计算、重复请求。
- 兼容性：浏览器差异、CSS 作用域、TS 类型收敛。
- 可维护性：提取公共逻辑、样式复用、目录结构清晰。

## 输出格式
- 优化摘要（目标、范围、风险）
- 改动建议（按文件或规则分组）
- 验证步骤（lint/build/页面回归）
