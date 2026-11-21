import { defineTool } from "@deepseek-ai/dsh-tools";
import { execFile } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

//#region helpers
/** 插件包内 CLI 的绝对路径。 */
const CLI = fileURLToPath(new URL("../lecture_cli.py", import.meta.url));

/** 大件（模型/依赖/ffmpeg）默认目录，可用 LECTURE_HOME 覆盖。 */
function homeDir() {
	return process.env.LECTURE_HOME || join(homedir(), ".dsh", "lecture");
}

/** 找一个能跑 numpy + faster_whisper 的 Python。 */
function pythonPath() {
	const cands = [
		process.env.LECTURE_PYTHON,
		join(homeDir(), "python", "python.exe"),
		"D:\\python\\python.exe",
		"python"
	].filter(Boolean);
	for (const c of cands) {
		if (c === "python") return c;
		if (existsSync(c)) return c;
	}
	return "python";
}

/** 跑 CLI 并取最后一行 JSON。 */
function runCli(argv, timeoutMs, signal) {
	return new Promise((resolvePromise, reject) => {
		execFile(pythonPath(), [CLI, ...argv], {
			timeout: timeoutMs,
			maxBuffer: 8 * 1024 * 1024,
			signal,
			windowsHide: true
		}, (err, stdout, stderr) => {
			const line = String(stdout).trim().split(/\r?\n/).filter(Boolean).pop() || "";
			let parsed;
			try { parsed = JSON.parse(line); } catch { parsed = undefined; }
			if (parsed !== undefined) return resolvePromise(parsed);
			const tail = (String(stderr) || String(err?.message) || "").trim().slice(-600);
			if (err?.killed) return resolvePromise({ ok: false, reason: "killed", detail: tail });
			reject(new Error(`lecture CLI failed: ${err?.message ?? "no output"}\n${tail}`));
		});
	});
}

/** 输出目录里已有的成品（用于缓存命中判断）。 */
function cachedTranscript(outDir, name) {
	const txt = join(outDir, `${name}.txt`);
	if (!existsSync(txt)) return undefined;
	try {
		return { transcript: txt, chars: statSync(txt).size };
	} catch {
		return undefined;
	}
}
//#endregion

/** Cordis plugin name. */
const name = "lecture-asr";
/** Services used by the tools. */
const inject = ["tools"];

/**
 * 离线讲座转写：任意音视频 → 逐字稿 + 字幕，纯 CPU、断点续跑。
 * 模型与依赖集中在 LECTURE_HOME（默认 ~/.dsh/lecture），不占项目目录。
 */
function apply(ctx) {
	ctx.tools.register(defineTool({
		name: "lecture_probe",
		description: "读取音视频文件的时长与流信息（不转写）。用于判断录播长度、是否有音轨，决定是否值得整段转写。",
		parameters: {
			media: { type: "string", required: true, description: "音视频文件绝对路径（mp4/mkv/mp3/wav…）。" }
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				properties: {
					duration_s: { type: "number", required: true, description: "时长（秒）。" },
					has_video: { type: "boolean", required: true },
					has_audio: { type: "boolean", required: true },
					human: { type: "string", required: true, description: "形如 42:54 的时长。" }
				}
			},
			render: (_args, v) => [{ type: "text", text: `${v.human}｜音轨 ${v.has_audio ? "有" : "无"}｜画面 ${v.has_video ? "有" : "无"}` }]
		},
		async execute(args, exec) {
			const media = resolve(args.media);
			if (!existsSync(media)) throw new Error(`media not found: ${media}`);
			const r = await runCli(["probe", media], 120_000, exec?.signal);
			const d = Number(r.duration_s) || 0;
			const human = `${String(Math.floor(d / 60)).padStart(2, "0")}:${String(d % 60).padStart(2, "0")}`;
			return { duration_s: d, has_video: Boolean(r.has_video), has_audio: Boolean(r.has_audio), human };
		}
	}));

	ctx.tools.register(defineTool({
		name: "lecture_transcribe",
		description: "把讲座/会议录播离线转成逐字稿（.txt）与字幕（.srt）。纯 CPU faster-whisper，分段并行、断点续跑（中断后重复调用会跳过已完成分段）。适合先从长视频拿到文字再写笔记、摘要、检索。",
		parameters: {
			media: { type: "string", required: true, description: "音视频文件绝对路径。" },
			out_dir: { type: "string", description: "产物目录；默认与媒体同级的 lecture_out/<文件名>。" },
			lang: { type: "string", description: "语言代码，默认 zh（中文讲座）。" },
			workers: { type: "number", description: "并行进程数，默认 4；按 CPU 核数×0.2 估算即可。" },
			threads: { type: "number", description: "每个进程的 CPU 线程数，默认 5。" },
			timeout_s: { type: "number", description: "本次调用最长等待秒数，默认 1200（20 分钟）；超时返回已完成进度，可再次调用续跑。" },
			force: { type: "boolean", description: "已存在逐字稿时是否重转，默认 false（命中缓存直接返回）。" }
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				properties: {
					ok: { type: "boolean", required: true },
					transcript: { type: "string", required: true, description: "逐字稿绝对路径。" },
					srt: { type: "string", description: "字幕绝对路径。" },
					chars: { type: "number", description: "正文字符数。" },
					segments: { type: "number", description: "句段数。" },
					duration_s: { type: "number", description: "音频时长（秒）。" },
					elapsed_s: { type: "number", description: "本次耗时（秒）。" },
					cached: { type: "boolean", description: "是否命中已有成品。" },
					partial: { type: "boolean", description: "是否因超时/失败只完成了一部分（可续跑）。" },
					note: { type: "string", description: "异常或续跑提示。" }
				}
			},
			render: (_args, v) => [{
				type: "text",
				text: v.ok
					? `${v.cached ? "命中缓存" : "转写完成"}：${v.chars} 字 / ${v.segments} 句 → ${v.transcript}`
					: `未完成（可续跑）：${v.note ?? "未知原因"} → ${v.transcript}`
			}]
		},
		async execute(args, exec) {
			const media = resolve(args.media);
			if (!existsSync(media)) throw new Error(`media not found: ${media}`);
			const stem = basename(media, extname(media));
			const outDir = resolve(args.out_dir || join(media, "..", "lecture_out", stem));
			if (!args.force) {
				const hit = cachedTranscript(outDir, stem);
				if (hit) return { ok: true, transcript: hit.transcript, chars: hit.chars, segments: 0, cached: true };
			}
			const timeoutMs = Math.max(60, Number(args.timeout_s) || 1200) * 1000;
			const argv = ["run", media, "--out", outDir, "--name", stem,
				"--lang", args.lang || "zh",
				"--workers", String(Math.max(1, Math.min(12, Number(args.workers) || 4))),
				"--threads", String(Math.max(1, Math.min(16, Number(args.threads) || 5)))];
			if (timeoutMs > 0) argv.push("--timeout", String(Math.floor(timeoutMs / 1000) - 15));
			const r = await runCli(argv, timeoutMs + 10_000, exec?.signal);
			const txt = r.transcript || join(outDir, `${stem}.txt`);
			if (r.ok) {
				return {
					ok: true,
					transcript: txt,
					srt: r.srt,
					chars: Number(r.chars) || 0,
					segments: Number(r.segments) || 0,
					duration_s: Number(r.duration_s) || 0,
					elapsed_s: Number(r.elapsed_s) || 0
				};
			}
			const done = Number(r.segments_done) || 0;
			const total = Number(r.segments_total) || 0;
			return {
				ok: false,
				transcript: txt,
				partial: true,
				note: `已完成 ${done}/${total} 段（${r.reason || "未完成"}），再次调用同一参数即可续跑。`
			};
		}
	}));

	ctx.tools.register(defineTool({
		name: "lecture_status",
		description: "查看某次转写的进度（分段完成数、百分比、当前阶段），用于长录音在后台跑时的轮询。",
		parameters: {
			out_dir: { type: "string", required: true, description: "lecture_transcribe 使用的产物目录。" }
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				properties: {
					stage: { type: "string", description: "当前阶段：probe/audio/transcribe/done/timeout。" },
					segments_done: { type: "number" },
					segments_total: { type: "number" },
					pct: { type: "number" },
					updated: { type: "string" }
				}
			},
			render: (_args, v) => [{
				type: "text",
				text: `${v.stage ?? "unknown"}｜${v.segments_done ?? 0}/${v.segments_total ?? 0} 段（${v.pct ?? 0}%）｜${v.updated ?? "-"}`
			}]
		},
		async execute(args, exec) {
			const dir = resolve(args.out_dir);
			const r = await runCli(["status", dir], 30_000, exec?.signal);
			return {
				stage: r.stage,
				segments_done: Number(r.segments_done) || 0,
				segments_total: Number(r.segments_total) || 0,
				pct: Number(r.pct) || 0,
				updated: r.updated
			};
		}
	}));
}

export { apply, inject, name };
