# CSS RTL 截断显尾技巧

## 适用场景
- 表格单元格或卡片中显示路径、长 URL、部门层级等字符串
- 文本溢出时使用 `text-overflow: ellipsis` 默认会截断尾部，但有时（如路径）尾部信息更重要

## 核心用法

### 一行 CSS 即可
```css
.dept-cell {
    max-width: 140px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    direction: rtl;         /* 关键：从右向左排列，ellipsis 出现在左侧 */
    text-align: right;      /* 配合 RTL 保持视觉对齐 */
}
```

## 效果对比

| 场景 | 无 RTL（默认） | 有 RTL |
|------|--------------|--------|
| 部门路径 | `集团/技术中心/AI研...` | `.../技术中心/AI研发部` |
| 文件路径 | `/usr/local/lib/pyth...` | `.../lib/python3.11/site` |
| 长 URL | `https://example.com/a...` | `...om/api/v2/users?page=3` |

## 注意事项
- 仅适用于单行截断场景（`white-space: nowrap`）
- 多行截断（`-webkit-line-clamp`）不适用此方法
- `text-align: right` 可让内容从右侧开始显示，确保尾部可见
- 对于中英文混排内容，`direction: rtl` 可能影响光标和选区行为，仅用于展示型单元格
