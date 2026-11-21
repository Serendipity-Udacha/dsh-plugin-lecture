// 冒烟测试：在没有 DSH 运行时的情况下加载插件，用桩 defineTool 校验结构，
// 再真实调用 lecture_probe 走一遍 python CLI。
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const stubDir = join(root, "node_modules", "@deepseek-ai", "dsh-tools");
mkdirSync(stubDir, { recursive: true });
writeFileSync(join(stubDir, "package.json"), JSON.stringify({ name: "@deepseek-ai/dsh-tools", type: "module", main: "index.js" }));
writeFileSync(join(stubDir, "index.js"), `
export function defineTool(o) {
  if (!o.name) throw new Error("missing tool name");
  if (!o.description) throw new Error("missing description: " + o.name);
  if (!o.parameters || typeof o.parameters !== "object") throw new Error("bad parameters: " + o.name);
  for (const [k, v] of Object.entries(o.parameters)) {
    if (!v || !v.type) throw new Error(\`param \${k} of \${o.name} missing type\`);
  }
  if (!o.output || !o.output.schema) throw new Error("missing output.schema: " + o.name);
  if (typeof o.output.render !== "function") throw new Error("missing output.render: " + o.name);
  if (typeof o.execute !== "function") throw new Error("missing execute: " + o.name);
  return o;
}
`);

try {
  const mod = await import(pathToFileURL(join(root, "lib", "index.js")).href);
  const tools = [];
  mod.apply({ tools: { register: (t) => tools.push(t) } });
  console.log("plugin:", mod.name, "| inject:", mod.inject, "| tools:", tools.map((t) => t.name).join(", "));

  const probe = tools.find((t) => t.name === "lecture_probe");
  const media = process.argv[2];
  if (media) {
    const out = await probe.execute({ media }, {});
    console.log("probe result:", JSON.stringify(out));
    console.log("render:", probe.output.render({}, out)[0].text);
  }
  if (process.argv[3] === "transcribe") {
    const tx = tools.find((t) => t.name === "lecture_transcribe");
    const out = await tx.execute({ media, workers: 1, threads: 6, timeout_s: 300, force: true }, {});
    console.log("transcribe result:", JSON.stringify(out));
    console.log("render:", tx.output.render({}, out)[0].text);
  }
} finally {
  rmSync(join(root, "node_modules"), { recursive: true, force: true });
}
