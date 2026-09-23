# Translation plan format (version 1)

`prepare` 生成 `manifest.json`、空 `plan.json`、预填 `plan.template.json` 和 `translation-brief.json`。模板含 `source_text` 供就地翻译，并覆盖所有文本块；brief 给每个待译块附上同页相邻内容和复核提示，便于按上下文翻译。将模板复制为 plan 后填写 html、检查建议 keep 和布局，再设置顶层 `review_required: false`。`scaffold --job <DIR>` 可为旧工作目录生成模板和 brief，默认不覆盖已有模板，也不会覆盖 plan。引用 ID 只对本次源文件快照和固定版本引擎有效，不能把上次课件的坐标硬套到新文件。

模板按现有文本块预填，不会自动跨栏或跨段合并。对于拆成多个小块的一条句子，可在同一个 edit 中合并 refs。未知人名、图表说明和含公式的自然语言仍需人工判断；启发式 math_formula 也可能误判，不能跳过 review_required。

## 内容与引用

- `p004.b002`：第 4 页的第 2 号提取块。页号从 1 开始；块号从 0 开始，可能跳号（图片块不输出为文本）。
- `p005.b004.s004`：该块中第 4 号 span（从 0 开始），用于只替换公式旁的说明文字。
- span 的 `text`、`bbox`、`size`、`color`、`font` 均在 manifest 中。`source.txt` 只列整块，精确修改才查看 span。
- 坐标为 PDF 点，左上角为原点，格式 `[x0, y0, x1, y1]`。1 点 = 1/72 英寸。
- `edits` 和 `keep` 必须合计覆盖所有有文字的 span，且不能重复。允许保留姓名、页脚、独立公式、表格数值等，但每项写明原因。程序仅检查覆盖和几何，不判断保留理由是否恰当。

```json
{
  "schema_version": 1,
  "source_sha256": "从 prepare 的 plan.json 原样保留",
  "target_language": "zh-CN",
  "edits": [
    {
      "refs": ["p001.b000"],
      "html": "学习目标",
      "box": [60, 45, 650, 90],
      "font_size": 32,
      "color": "#650034",
      "fill": "#ffffff"
    },
    {
      "refs": ["p001.b001", "p001.b002"],
      "html": "• 求两个随机变量的<b>协方差</b>，记作 <span class=\"math\">Cov(X, Y)</span>。",
      "box": [65, 140, 675, 230],
      "font_size": 23,
      "min_scale": 0.82,
      "align": "left"
    }
  ],
  "keep": [
    {"refs": ["p001.b003"], "reason": "独立公式，保留原字形与排版"},
    {"refs": ["p001.b004"], "reason": "版权与页码保留原文"}
  ]
}
```

示例 ID 不对应任何特定课件；必须使用当前 manifest 的 ID。不要把示例占位哈希写进实际规划。

## 字段

`refs`、`html` 为每个 edit 的必填项，其余为可选。

| 字段 | 默认与含义 |
| --- | --- |
| `refs` | 同一页上一个或多个块/span，不能跨页。所引源文字将删除。 |
| `html` | 译文，使用下述受限 HTML。 |
| `box` | 所有 refs 的外接矩形。译文优先排在此框；auto 模式仅在溢出时尝试有限安全扩框，strict 模式不改框。 |
| `font_size` | 来源 span 的最大字号。标题/正文有需要时显式调整。 |
| `min_scale` | 0.82；最小允许缩放，范围 0.5–1。达不到仍放不下就失败。 |
| `align` | `left`；还支持 `center`、`right`、`justify`。 |
| `color` | 首个源 span 的颜色，`#RRGGBB`。正文通常显式写黑色。 |
| `fill` | `auto`；从矢量填色与边缘像素推断纯色背景，无法确定时拒绝执行。也可显式指定 `#RRGGBB`。 |

`box` 不能与其他原文（即便那块也将翻译）、其他译文框或图片内容相交。图片自带且匹配 `fill` 的空白边缘可以接触；存在旋转或无法读取的图片则不放行。脚本按原文行删除，而不是抹去整个段落外接矩形。若相邻的同一段落被拆成多个块，应合并 refs 成一项；不要忽略冲突去强行覆盖。脚本会保留独立公式原对象，包括提取成奇怪字符的积分号/求和号。

公式所在段落若包含普通叙述，可整体翻译并用 HTML 重新输入少量行内记号。复杂公式优先拆出 span 保留；如果矩形相交无法安全分离，就保留该区域并说明，不假装已完整翻译。

支持 `b`、`strong`、`i`、`em`、`sub`、`sup`、`br` 和 `span class="math"`。例如：

```html
<span class="math">p<sub>X,Y</sub>(x, y)</span>
<span class="math">a<sup>2</sup>Var(X)</span>
```

不接受图片、链接、任意 CSS、文件路径、脚本或外部资源。文字中的小于号和 `&` 应分别写为 `&lt;`、`&amp;`。翻译每个段落时参考整页和前后页，不按 span 独立直译。

## 修订、恢复和验收

修改 `plan.json` 的相关条目，再运行 `apply`。源快照哈希不匹配必须重新 prepare；不要手改哈希绕过。目标 PDF 已存在时默认拒绝覆盖，重跑本任务自己的候选结果可显式用 `--overwrite`。

`qa.json` 给出自动检查结果、每项实际缩放、保留项、源页警告和输出文件哈希。`visual_review` 固定初始为待检查，不能把它理解为视觉检查通过。agent 看完实际页面后才报告视觉结论。脚本不校对译文，不解题，也不验证原课件公式本身是否正确。

新增的 `typography_warnings` 为非阻断提醒，包括 `orphan_character_detected`（多行文本末行只有 1–2 个汉字）、`line_start_punctuation` 和 `line_end_opening_punctuation`。有意的短行可以保留；不能因为 warning 擅自改写意思。`optimization` 含采用的子集化方法、保存体积及残留大字体警告，fonttools 回退只有逐页视觉一致且体积变小时才采用。

只译部分页时，未选页的所有文字显式列入 keep，并在理由里注明用户选择的页范围；输出仍保持全部原页位置。这不是删除未选页的命令。

## 模板建议与批量编辑

`layout_recommendations` 是提示数据，apply 不直接信任其坐标：`widen_right` 提供有限向右扩框，`clamp_above_image` 在原文行本身不碰图片时提供上沿收剪，`merge_continuation` 提供疑似续句的 refs 和 union box。auto 模式会独立检测图片收剪和空白扩框；strict 模式不会自动调整。语义合并始终需要检查实际页面并移除原有两项，重新执行 apply 的完整检查。多个建议不能直接同时叠加；收剪后能否容纳译文仍由排版引擎验证。

`math_fragment` 表示邻近强公式块的纯数学片段，`math_context_refs` 记录关联依据；它是待复核 keep，不是公式识别保证。

长文档建议用 `json.load` / `json.dump(..., ensure_ascii=False, indent=2)` 读取模板、按稳定 refs 映射填入译文并应用明确规则，保留可重现脚本。不要重新构造源哈希或无依据清除 review_required；填写完成并逐项复核后才解除复核标记。

## 快速填写和迭代

可直接填写 `translation-brief.json` 中的 `translations` 和 `glossary`，再把该文件传给 `fill`。brief 中的 `source_sha256` 会阻止把译文误用于另一份 PDF。

`fill --job <DIR> --translations <JSON> [--reviewed]` 接受 `{块ID: HTML译文}`，要求 keys 与模板 edits 一一对应，不包含 keep。也可传入 `{"translations": {块ID: HTML译文}, "glossary": [{"source": "sample mean", "target": "样本均值"}]}`。术语表逐项检查原文是否包含源词、对应译文是否包含目标词；结果写入 `terminology-warnings.json`，存在缺失时不能使用 `--reviewed`。这是字面匹配提示，词形变化、同义改写仍须人工复核。它沿用模板几何、哈希及 keep 决策，不自动批准复核；检查完 keep 和译文后才传 `--reviewed`。已有非空 plan 需显式 `--overwrite` 才能替换。合并后的多 refs 计划直接编辑，不经 fill 重建。

apply 默认 `--layout auto --mode both`。自动收剪只在原文行没有碰图片时尝试；扩框只在溢出后尝试，检查其他原文、译文框、图片及新增区域的实际像素，保持 min_scale 下限。不能修复的几何问题仍阻断执行。不会自动合并正文、遗漏译文或跨页重排。

`--mode both` 默认生成译文版及每页左右并排的 `_dual.pdf`。`--mode side-by-side` 只生成左右版；`--mode interleaved` 保留旧式原页、译页交替输出。左右版只组合已完成的原页和译页，不改变原位翻译的排版限制。

最终 `qa.json` 的 `content_review_warnings` 提醒保留项中的英文页脚、公式连接词、较大图片中的待核文字，以及译文复制/搜索时可能出现的私用区字符。它不拦截有意保留的版权或书目信息。可用 `audit --job <DIR> --translated <译文PDF>` 对已有成品只读复查，不重新生成 PDF。

`--draft` 跳过压缩与对照合成，输出独立 `_draft.pdf` 和 `qa-draft.json`，不覆盖最终交付；正常 apply 输出 `qa.json`。`--preview changed` 的联系表只代表报告中的 preview_pages，不能误读为整个文档。

按页缓存仅复用排版结果，完整自动校验仍执行。`--no-cache` 可禁用；修改源码、字体、源文件或布局模式会使缓存失效。溢出时 `apply-errors.json` 集中列出全部失败项，成功页保存在工作目录缓存中，失败 PDF 不发布。下一次成功 apply 会清除旧错误报告。`timings_seconds` 不含翻译推理或人工复核时间。
