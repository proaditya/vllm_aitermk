// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: Copyright contributors to the vLLM project

import { appendFileSync, existsSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import type {
	ExtensionAPI,
	ExtensionContext,
} from "@earendil-works/pi-coding-agent";

const PROVIDER = "libaitermk-vllm";
const FILE_TOOLS = new Set(["read", "edit", "write", "grep", "find", "ls"]);
const WRITE_TOOLS = new Set(["edit", "write"]);

type Policy = "allow" | "ask" | "deny";

interface RequestTiming {
	step: number;
	startedAt: number;
	firstDeltaAt?: number;
	streamEvents: number;
	lastStatusUpdateAt: number;
	httpStatus?: number;
}

interface CompletedMetrics {
	step: number;
	promptTokens: number;
	outputTokens: number;
	ttftMs?: number;
	tpotMs?: number;
	decodeTokensPerSecond?: number;
	endToEndTokensPerSecond: number;
	elapsedMs: number;
	streamEvents: number;
	httpStatus?: number;
	timestamp: string;
}

function positiveInteger(name: string, fallback: number): number {
	const value = Number.parseInt(process.env[name] ?? "", 10);
	return Number.isInteger(value) && value > 0 ? value : fallback;
}

function readPolicy(name: string): Policy {
	const value = process.env[name] ?? "ask";
	if (value === "allow" || value === "ask" || value === "deny") {
		return value;
	}
	throw new Error(`${name} must be allow, ask, or deny; got ${value}`);
}

function isWithin(root: string, candidate: string): boolean {
	const pathFromRoot = relative(root, candidate);
	return (
		pathFromRoot === "" ||
		(!pathFromRoot.startsWith(`..${sep}`) &&
			pathFromRoot !== ".." &&
			!isAbsolute(pathFromRoot))
	);
}

function canonicalizePotentialPath(path: string): string {
	if (existsSync(path)) {
		return realpathSync(path);
	}

	let ancestor = path;
	while (!existsSync(ancestor)) {
		const parent = dirname(ancestor);
		if (parent === ancestor) {
			return path;
		}
		ancestor = parent;
	}

	const canonicalAncestor = realpathSync(ancestor);
	return resolve(canonicalAncestor, relative(ancestor, path));
}

function validateWorkspacePath(cwd: string, inputPath: string): string | undefined {
	const workspace = realpathSync(cwd);
	const candidate = canonicalizePotentialPath(resolve(workspace, inputPath));
	if (!isWithin(workspace, candidate)) {
		return `Path is outside the workspace: ${inputPath}`;
	}
	return undefined;
}

async function approve(
	policy: Policy,
	ctx: ExtensionContext,
	title: string,
	detail: string,
): Promise<boolean> {
	if (policy === "allow") return true;
	if (policy === "deny" || !ctx.hasUI) return false;
	return ctx.ui.confirm(title, detail);
}

function milliseconds(value: number | undefined): string {
	return value === undefined ? "n/a" : `${value.toFixed(2)} ms`;
}

function rate(value: number | undefined): string {
	return value === undefined ? "n/a" : `${value.toFixed(2)} tok/s`;
}

function recordMetrics(metrics: CompletedMetrics): void {
	const metricsFile = process.env.LIBAITERMK_PI_METRICS_FILE;
	if (!metricsFile) return;
	try {
		appendFileSync(metricsFile, `${JSON.stringify(metrics)}\n`, "utf8");
	} catch (error) {
		console.error(`Unable to write Pi metrics to ${metricsFile}: ${error}`);
	}
}

function showMetrics(metrics: CompletedMetrics, ctx: ExtensionContext): void {
	const summary = [
		`step ${metrics.step}`,
		`prompt=${metrics.promptTokens}`,
		`output=${metrics.outputTokens}`,
		`TTFT=${milliseconds(metrics.ttftMs)}`,
		`TPOT=${milliseconds(metrics.tpotMs)}`,
		`decode=${rate(metrics.decodeTokensPerSecond)}`,
		`e2e=${rate(metrics.endToEndTokensPerSecond)}`,
	].join(" | ");

	if (ctx.hasUI) {
		ctx.ui.setStatus(
			"libaitermk-metrics",
			`step ${metrics.step} | TPOT ${milliseconds(metrics.tpotMs)} | ${rate(metrics.decodeTokensPerSecond)}`,
		);
		ctx.ui.setWidget(
			"libaitermk-metrics",
			[
				`libAiterMK step ${metrics.step}: prompt ${metrics.promptTokens}, output ${metrics.outputTokens}`,
				`TTFT ${milliseconds(metrics.ttftMs)} | TPOT ${milliseconds(metrics.tpotMs)} | decode ${rate(metrics.decodeTokensPerSecond)} | e2e ${rate(metrics.endToEndTokensPerSecond)}`,
			],
			{ placement: "aboveEditor" },
		);
	} else {
		console.error(summary);
	}
}

export default function libAiterMKExtension(pi: ExtensionAPI): void {
	const endpoint = process.env.VLLM_CHAT_URL ?? "http://127.0.0.1:8000/v1";
	const model = process.env.VLLM_CHAT_MODEL ?? "gpt-oss-120b";
	const apiKey = process.env.VLLM_CHAT_API_KEY ?? "EMPTY";
	const contextWindow = positiveInteger("VLLM_CHAT_CONTEXT_WINDOW", 65536);
	const maxTokens = positiveInteger("VLLM_CHAT_MAX_TOKENS", 4096);
	const writePolicy = readPolicy("LIBAITERMK_PI_WRITE_POLICY");
	const shellPolicy = readPolicy("LIBAITERMK_PI_SHELL_POLICY");
	const statsEnabled = process.env.LIBAITERMK_PI_STATS === "1";

	let request: RequestTiming | undefined;
	let lastMetrics: CompletedMetrics | undefined;
	let step = 0;

	pi.registerProvider(PROVIDER, {
		name: "libAiterMK vLLM",
		baseUrl: endpoint,
		apiKey,
		api: "openai-completions",
		models: [
			{
				id: model,
				name: `${model} (libAiterMK)`,
				reasoning: false,
				input: ["text"],
				cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
				contextWindow,
				maxTokens,
				compat: {
					supportsStore: false,
					supportsDeveloperRole: false,
					supportsReasoningEffort: false,
					supportsUsageInStreaming: true,
					maxTokensField: "max_tokens",
					supportsStrictMode: false,
				},
			},
		],
	});

	pi.on("tool_call", async (event, ctx) => {
		if (FILE_TOOLS.has(event.toolName)) {
			const inputPath = (event.input as { path?: unknown }).path;
			if (typeof inputPath === "string") {
				const pathError = validateWorkspacePath(ctx.cwd, inputPath);
				if (pathError) return { block: true, reason: pathError };
			}
		}

		if (WRITE_TOOLS.has(event.toolName)) {
			const inputPath = (event.input as { path?: unknown }).path;
			const allowed = await approve(
				writePolicy,
				ctx,
				"Allow file modification?",
				`${event.toolName}: ${String(inputPath ?? "unknown path")}`,
			);
			if (!allowed) {
				return { block: true, reason: `Write blocked by ${writePolicy} policy` };
			}
		}

		if (event.toolName === "bash") {
			const command = String((event.input as { command?: unknown }).command ?? "");
			const allowed = await approve(
				shellPolicy,
				ctx,
				"Allow shell command?",
				command,
			);
			if (!allowed) {
				return { block: true, reason: `Shell blocked by ${shellPolicy} policy` };
			}
		}

		return undefined;
	});

	if (!statsEnabled) return;

	pi.on("before_provider_request", (_event, ctx) => {
		step += 1;
		request = {
			step,
			startedAt: performance.now(),
			streamEvents: 0,
			lastStatusUpdateAt: 0,
		};
		if (ctx.hasUI) {
			ctx.ui.setStatus("libaitermk-metrics", `step ${step} | waiting for first token`);
		}
	});

	pi.on("after_provider_response", (event) => {
		if (!request) return;
		request.httpStatus = event.status;
	});

	pi.on("message_update", (event, ctx) => {
		if (!request) return;
		const eventType = event.assistantMessageEvent.type;
		if (
			eventType !== "text_delta" &&
			eventType !== "thinking_delta" &&
			eventType !== "toolcall_delta"
		) {
			return;
		}

		const now = performance.now();
		request.firstDeltaAt ??= now;
		request.streamEvents += 1;
		if (ctx.hasUI && now - request.lastStatusUpdateAt >= 100) {
			const elapsedSeconds = (now - request.startedAt) / 1000;
			ctx.ui.setStatus(
				"libaitermk-metrics",
				`step ${request.step} | ${request.streamEvents} stream chunks | ${elapsedSeconds.toFixed(1)}s`,
			);
			request.lastStatusUpdateAt = now;
		}
	});

	pi.on("message_end", (event, ctx) => {
		if (!request || event.message.role !== "assistant") return;

		const endedAt = performance.now();
		const usage = event.message.usage;
		const promptTokens = usage.input + usage.cacheRead + usage.cacheWrite;
		const outputTokens = usage.output;
		const elapsedMs = endedAt - request.startedAt;
		const ttftMs = request.firstDeltaAt
			? request.firstDeltaAt - request.startedAt
			: undefined;
		const decodeMs = request.firstDeltaAt ? endedAt - request.firstDeltaAt : undefined;
		const tpotMs =
			decodeMs !== undefined && outputTokens > 1
				? decodeMs / (outputTokens - 1)
				: undefined;
		const decodeTokensPerSecond =
			decodeMs !== undefined && decodeMs > 0 && outputTokens > 1
				? ((outputTokens - 1) * 1000) / decodeMs
				: undefined;

		lastMetrics = {
			step: request.step,
			promptTokens,
			outputTokens,
			ttftMs,
			tpotMs,
			decodeTokensPerSecond,
			endToEndTokensPerSecond:
				elapsedMs > 0 ? (outputTokens * 1000) / elapsedMs : 0,
			elapsedMs,
			streamEvents: request.streamEvents,
			httpStatus: request.httpStatus,
			timestamp: new Date().toISOString(),
		};

		recordMetrics(lastMetrics);
		showMetrics(lastMetrics, ctx);
		request = undefined;
	});

	pi.registerCommand("libaitermk-metrics", {
		description: "Show metrics for the most recent model step",
		handler: async (_args, ctx) => {
			if (!lastMetrics) {
				ctx.ui.notify("No completed libAiterMK model step yet", "info");
				return;
			}
			showMetrics(lastMetrics, ctx);
		},
	});
}
