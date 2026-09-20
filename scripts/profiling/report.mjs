import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { root } from "../process.mjs";
import { signature } from "./measure.mjs";

const directory = resolve(root, "artifacts/profiling");
const labels = {
  total: "请求主体与析构余项",
  "setup.position": "重建棋盘",
  "setup.evaluator": "评估器构造与依赖表预计算",
  search: "搜索递归、路径维护等余项",
  "search.checks": "时间、取消与节点上限检查",
  "board.candidates": "候选落点生成",
  "search.ranking": "成五/防守检查与几何打分",
  "search.sorting": "候选选择与排序",
  "board.play": "棋盘落子、胜负检查与哈希更新",
  "board.undo": "棋盘撤销与哈希恢复",
  "nnue.reset": "NNUE 全量初始化（父阶段）",
  "nnue.reset.features": "初始化线特征",
  "nnue.reset.spatial": "初始化空间卷积与池化",
  "nnue.push": "NNUE 落子更新（父阶段）",
  "nnue.save": "保存特征与池化撤销状态",
  "nnue.features": "增量线编码、查表与特征合并",
  "nnue.spatial": "增量空间卷积、量化与池化更新",
  "nnue.pop": "恢复 NNUE 撤销状态",
  "nnue.value": "Value 头（父阶段）",
  "nnue.value.input": "Value 池化均值与输入组装",
  "nnue.value.hidden": "Value 隐藏层矩阵乘法",
  "nnue.value.activation": "Value 隐藏层激活",
  "nnue.value.output": "Value 输出层矩阵乘法",
  "nnue.score": "WDL logits 转搜索分数",
  "nnue.policy": "Policy 头（父阶段）",
  "nnue.policy.input": "Policy 池化均值与全局输入",
  "nnue.policy.global": "Policy 全局投影",
  "nnue.policy.pair": "Policy 双视角局部输入复制",
  "nnue.policy.local": "Policy 局部投影矩阵乘法",
  "nnue.policy.activation": "Policy 激活与全局特征融合",
  "nnue.policy.output": "Policy 输出层矩阵乘法",
};
const groups = [
  { label: "空间卷积与池化更新", match: (name) => name === "nnue.spatial" },
  {
    label: "Value 头与分数转换",
    match: (name) => name.startsWith("nnue.value") || name === "nnue.score",
  },
  { label: "Policy 头", match: (name) => name.startsWith("nnue.policy") },
  { label: "线特征增量更新", match: (name) => name === "nnue.features" },
  {
    label: "NNUE 状态保存与撤销",
    match: (name) => ["nnue.push", "nnue.save", "nnue.pop"].includes(name),
  },
  { label: "候选生成", match: (name) => name === "board.candidates" },
  {
    label: "战术/几何打分与排序",
    match: (name) => ["search.ranking", "search.sorting"].includes(name),
  },
  { label: "时间与取消检查", match: (name) => name === "search.checks" },
  { label: "棋盘落子与撤销", match: (name) => ["board.play", "board.undo"].includes(name) },
  {
    label: "搜索前初始化",
    match: (name) => name.startsWith("setup.") || name.startsWith("nnue.reset"),
  },
  { label: "搜索及请求其余开销", match: (name) => name === "search" || name === "total" },
];
const reports = [];
const historical = JSON.parse(
  await readFile(resolve(root, "docs/benchmarks/2026-09-21-before.json"), "utf8"),
);
for (const runtime of ["browser-wasm", "wasm", "native"]) {
  let raw;
  try {
    raw = await readFile(resolve(directory, `${runtime}.json`), "utf8");
  } catch (error) {
    if (error.code === "ENOENT") continue;
    throw error;
  }
  const report = JSON.parse(raw);
  const before = historical.find((item) => item.metadata.runtime === runtime);
  for (const row of report.rows) {
    if (reports.length) {
      const other = reports[0].rows.find((item) => item.id === row.id);
      if (signature(row.baseline) !== signature(other.baseline))
        throw new Error(`Native/WASM search mismatch: ${runtime} / ${row.id}`);
    }
    row.groups = groups.map(({ label, match }) => ({
      label,
      ms: row.profiled.phases
        .filter((phase) => match(phase.name))
        .reduce((sum, phase) => sum + phase.meanExclusiveMs, 0),
    }));
    const sum = row.groups.reduce((sum, group) => sum + group.ms, 0);
    const total = row.profiled.phases.find((phase) => phase.name === "total").meanInclusiveMs;
    if (Math.abs(sum - total) > 0.001) throw new Error("Grouped phases do not reconcile");
    for (const group of row.groups) group.percent = (100 * group.ms) / total;
    const original = before?.rows.find((item) => item.id === row.id);
    if (
      original &&
      before.metadata.model.sha256 === report.metadata.model.sha256 &&
      JSON.stringify(original.position) === JSON.stringify(row.position) &&
      original.maxDepth === row.maxDepth
    ) {
      if (
        JSON.stringify(original.baseline.result.score) !==
          JSON.stringify(row.baseline.result.score) ||
        JSON.stringify(original.baseline.result.bestMove) !==
          JSON.stringify(row.baseline.result.bestMove) ||
        original.baseline.result.depth !== row.baseline.result.depth
      )
        throw new Error(`Optimization changed the reference decision: ${runtime} / ${row.id}`);
      row.before = {
        medianMs: original.baseline.medianMs,
        nodes: original.baseline.result.nodes,
        speedup: original.baseline.medianMs / row.baseline.medianMs,
        phases: original.profiled.phases,
        metadata: {
          measuredAt: before.metadata.measuredAt,
          cpu: before.metadata.cpu,
          build: before.metadata.build,
        },
      };
    }
    row.searchStats = row.samples.baseline[0].stats;
  }
  reports.push(report);
}
if (!reports.length) throw new Error("Run the profiler first");
await mkdir(directory, { recursive: true });
const summary = reports.map(
  ({ metadata, repeats, warmups, startup, rows, iterations, timeLimited }) => ({
    metadata,
    repeats,
    warmups,
    startup,
    iterations,
    timeLimited,
    rows: rows.map(({ samples: _, position: __, ...row }) => row),
  }),
);
await writeFile(resolve(directory, "summary.json"), JSON.stringify(summary, null, 2) + "\n");
const csv = [
  [
    "runtime",
    "case",
    "phase",
    "label",
    "calls",
    "mean_exclusive_ms",
    "exclusive_percent",
    "mean_inclusive_ms",
    "mean_inclusive_us_per_call",
  ],
];
for (const report of reports)
  for (const row of report.rows)
    for (const phase of row.profiled.phases)
      csv.push([
        report.metadata.runtime,
        row.id,
        phase.name,
        labels[phase.name],
        phase.calls,
        phase.meanExclusiveMs,
        phase.exclusivePercent,
        phase.meanInclusiveMs,
        phase.meanInclusiveUsPerCall,
      ]);
await writeFile(
  resolve(directory, "phases.csv"),
  "\uFEFF" +
    csv
      .map((row) => row.map((cell) => '"' + String(cell).replaceAll('"', '""') + '"').join(","))
      .join("\n") +
    "\n",
);
const embedded = JSON.stringify({ reports: summary, labels }).replaceAll("<", "\\u003c");
const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Gomoku 搜索耗时报告</title>
<style>
:root{font:15px/1.6 system-ui,sans-serif;color:#242924;background:#fafbf9}body{max-width:1100px;margin:40px auto;padding:0 28px 60px}h1{font-size:26px;letter-spacing:-.5px;margin:0}h2{font-size:18px;margin:28px 0 14px}p,.muted{color:#626a62}.eyebrow{font-size:12px;letter-spacing:1px;color:#55705e;margin-bottom:8px}select{font:inherit;padding:8px 12px;border:1px solid #c4cdc5;border-radius:5px;background:white;max-width:100%}label{display:inline-flex;gap:12px;align-items:center;margin:0 22px 12px 0}.stats{display:flex;gap:38px;flex-wrap:wrap;margin:20px 0;padding:18px 0;border-top:1px solid #d4dcd4;border-bottom:1px solid #d4dcd4}.stat small{display:block;color:#626a62}.stat strong{font-size:22px;font-weight:600}.bar-row{display:grid;grid-template-columns:205px minmax(100px,1fr) 175px;gap:16px;align-items:center;margin:10px 0;font-size:13px}.track{height:20px;background:#e9ede8}.bar{height:100%;background:#52755e}.bar-row:first-child .bar{background:#214d32}.amount{text-align:right;font-variant-numeric:tabular-nums}.note{font-size:13px;max-width:940px}table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}th,td{padding:9px 10px;border-bottom:1px solid #dce2dc;text-align:right}th:first-child,td:first-child{text-align:left}th{font-weight:500;color:#606960}td code{font-size:11px;color:#768076;display:block}.scroll{overflow:auto}details{margin-top:24px}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;font-size:12px}a{color:#315e40}@media(max-width:660px){body{padding:0 16px}.bar-row{grid-template-columns:145px 1fr 125px;gap:8px}.stats{gap:20px}h1{font-size:23px}}
</style>
<div class="eyebrow">GOMOKU / PERFORMANCE</div><h1>搜索优化前后对照</h1>
<p id="subtitle"></p><label>运行环境<select id="runtime"></select></label><label>局面<select id="case"></select></label>
<div class="stats" id="stats"></div>
<p class="note" id="gain"></p>
<h2>同一模型、局面和完成深度</h2>
<div class="scroll"><table><thead><tr><th>局面</th><th>优化前 ms</th><th>当前 ms</th><th>加速倍数</th><th>节点变化</th></tr></thead><tbody id="before-after"></tbody></table></div>
<p class="note">优化前是 2026-09-21 在 Intel Core Ultra X7 358H 上留存的标量 AB 基线；当前是增量 NNUE、SIMD 与 TT/PVS。报告核对模型、局面、最佳着、评分与完成深度；复测时还需核对硬件与构建信息。前后并非同一时刻交错执行，排序可能改变等分主变化。没有添加五子棋专用战术搜索。</p>
<h2>当前每次完整搜索的耗时分布</h2>
<p class="note">横向条形：诊断构建平均耗时占比（%）；纵向分类：计算阶段。右侧为每次搜索平均毫秒数和占比。各行互斥，相加为 100%。</p><div id="bars"></div>
<p class="note" id="method"></p><h2>各阶段详细计时</h2><p class="note">自身耗时已扣除子阶段，可相加。含子阶段耗时不可重复相加。每次调用的均值由累计时间除以调用数得到，微小阶段会受到时钟精度与计时开销影响。</p>
<div class="scroll"><table><thead><tr><th>计算阶段</th><th>调用次数<br>优化前 → 当前</th><th>自身耗时<br>ms / 搜索</th><th>占比</th><th>含子阶段<br>ms / 搜索</th><th>单次调用 μs<br>优化前 → 当前</th></tr></thead><tbody id="phases"></tbody></table></div>
<details><summary>全部局面与运行环境对照</summary><div class="scroll"><table><thead><tr><th>环境 / 局面</th><th>深度</th><th>节点</th><th>普通构建中位数 ms</th><th>普通构建范围 ms</th><th>插桩差异</th></tr></thead><tbody id="comparison"></tbody></table></div></details>
<details><summary>复现方法、启动时间与原始测量边界</summary><pre id="metadata"></pre></details>
<script type="application/json" id="data">${embedded}</script><script>
const {reports,labels}=JSON.parse(document.querySelector('#data').textContent);
const names={'browser-wasm':'浏览器 Web Worker / WASM',wasm:'Node.js / WASM',native:'Windows 原生 C++'};
const rSelect=document.querySelector('#runtime'),cSelect=document.querySelector('#case');
const number=(x,d=2)=>x.toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d});
function cell(row,value){const td=document.createElement('td');td.textContent=value;row.append(td);return td;}
reports.forEach((r,i)=>rSelect.add(new Option(names[r.metadata.runtime],i)));
function setCases(){const previous=cSelect.value;cSelect.replaceChildren();reports[+rSelect.value].rows.forEach((row,i)=>cSelect.add(new Option(row.label,i)));if(previous)cSelect.value=previous;render();}
function render(){const report=reports[+rSelect.value],row=report.rows[+cSelect.value];
document.querySelector('#subtitle').textContent=report.metadata.cpu+' · '+report.metadata.model.id+' · '+report.repeats+' 次正式测量 / '+report.warmups+' 次预热 · '+new Date(report.metadata.measuredAt).toLocaleString('zh-CN');
const stats=document.querySelector('#stats');stats.replaceChildren();
for(const [title,value] of [['普通构建中位数',number(row.baseline.medianMs)+' ms'],['完成深度 / 节点',row.baseline.result.depth+' / '+number(row.baseline.result.nodes,0)],['诊断构建中位数',number(row.profiled.medianMs)+' ms'],['插桩测量差异',number(row.overheadPercent)+'%']]){const box=document.createElement('div');box.className='stat';const small=document.createElement('small');small.textContent=title;const strong=document.createElement('strong');strong.textContent=value;box.append(small,strong);stats.append(box);}
const limited=report.timeLimited.map(x=>x.result.depth),stat=row.searchStats;
document.querySelector('#gain').textContent=(row.before?'所选局面：'+number(row.before.medianMs)+' → '+number(row.baseline.medianMs)+' ms，速度 '+number(row.before.speedup)+' 倍。':'')+'300 ms 天元开局：'+Math.min(...limited)+'–'+Math.max(...limited)+' 层（'+limited.length+' 次）。'+(stat?'当前局面：TT 直接复用 '+stat.ttCutoffs+' 次，实际 NNUE 更新 '+stat.evaluatorPushes+' 次，缓存着法省去整批 policy '+stat.preferredCutoffs+' 次。':'');
const change=document.querySelector('#before-after');change.replaceChildren();for(const item of report.rows){if(!item.before)continue;const tr=document.createElement('tr');[item.label,number(item.before.medianMs),number(item.baseline.medianMs),number(item.before.speedup)+'×',number(item.before.nodes,0)+' → '+number(item.baseline.result.nodes,0)].forEach(x=>cell(tr,x));change.append(tr);}
const bars=document.querySelector('#bars');bars.replaceChildren();
for(const group of [...row.groups].sort((a,b)=>b.ms-a.ms)){const box=document.createElement('div');box.className='bar-row';const label=document.createElement('span');label.textContent=group.label;const track=document.createElement('div');track.className='track';const bar=document.createElement('div');bar.className='bar';bar.style.width=group.percent+'%';track.append(bar);const amount=document.createElement('span');amount.className='amount';amount.textContent=number(group.ms)+' ms / '+number(group.percent,1)+'%';box.append(label,track,amount);bars.append(box);}
document.querySelector('#method').textContent='普通构建 '+number(row.baseline.minMs)+'–'+number(row.baseline.maxMs)+' ms；诊断构建 '+number(row.profiled.minMs)+'–'+number(row.profiled.maxMs)+' ms。分阶段数据来自诊断构建，未按普通构建耗时缩放。模型已加载，排除下载、权重加载、JSON 编解码、Worker 消息与界面渲染；包含棋盘重建、评估器创建、搜索及析构。';
const phases=document.querySelector('#phases');phases.replaceChildren();for(const phase of [...row.profiled.phases].sort((a,b)=>b.meanExclusiveMs-a.meanExclusiveMs)){const old=row.before?.phases.find(x=>x.name===phase.name);const tr=document.createElement('tr');const title=cell(tr,labels[phase.name]);const code=document.createElement('code');code.textContent=phase.name;title.append(code);[(old?number(old.calls,0)+' → ':'')+number(phase.calls,0),number(phase.meanExclusiveMs,3),number(phase.exclusivePercent,2)+'%',number(phase.meanInclusiveMs,3),(old?number(old.meanInclusiveUsPerCall,3)+' → ':'')+number(phase.meanInclusiveUsPerCall,3)].forEach(x=>cell(tr,x));phases.append(tr);}
document.querySelector('#metadata').textContent='复现：pnpm engine:profile\\n浏览器：node scripts/profiling/server.mjs，然后打开 http://127.0.0.1:4177/ 点击开始测量\\n报告：node scripts/profiling/report.mjs\\n\\n'+JSON.stringify({metadata:report.metadata,startupSingleObservation:report.startup,iterations:report.iterations.iterations,timeLimited:report.timeLimited.map(x=>({wallMs:x.wallMs,result:x.result}))},null,2)+'\\n\\n启动仅测一次：原生包含本地读文件与解码；WASM 分别统计模块初始化、复制进堆、模型解码，下载及摘要校验不包含在 loadMs。\\n负的插桩差异表示计时噪声或代码布局差异，不代表计时能使程序加速。';
}
const comparison=document.querySelector('#comparison');for(const report of reports)for(const row of report.rows){const tr=document.createElement('tr');[names[report.metadata.runtime]+' / '+row.label,row.baseline.result.depth,number(row.baseline.result.nodes,0),number(row.baseline.medianMs),number(row.baseline.minMs)+'–'+number(row.baseline.maxMs),number(row.overheadPercent)+'%'].forEach(x=>cell(tr,x));comparison.append(tr);}
rSelect.onchange=setCases;cSelect.onchange=render;setCases();
</script></html>`;
await writeFile(resolve(directory, "report.html"), html);
for (const report of summary)
  console.log(
    JSON.stringify({
      runtime: report.metadata.runtime,
      cases: report.rows.map((row) => ({
        id: row.id,
        baselineMs: row.baseline.medianMs,
        profiledMs: row.profiled.medianMs,
        overhead: row.overheadPercent,
        top: [...row.groups].sort((a, b) => b.ms - a.ms).slice(0, 4),
      })),
    }),
  );
console.log(`Saved ${resolve(directory, "report.html")}; native/WASM search parity verified.`);
