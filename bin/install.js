#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const SKILL_SRC = path.join(__dirname, "..", "skills", "subscribr-api", "SKILL.md");
const CLI_SRC = path.join(__dirname, "..", "subscribr.py");

// Standard locations per Agent Skills spec + Claude Code
const AGENTS_DIR = path.join(process.cwd(), ".agents", "skills", "subscribr-api");
const CLAUDE_DIR = path.join(process.cwd(), ".claude", "skills", "subscribr-api");

function copyFile(src, dest, label) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
  console.log(`  \u2713 ${label} \u2192 ${path.relative(process.cwd(), dest)}`);
}

console.log("\n  @subscribr/cli\n");

// Install to .agents/skills/ (Codex, Cursor, Agent Skills spec)
copyFile(SKILL_SRC, path.join(AGENTS_DIR, "SKILL.md"), "Skill (.agents/skills/)");

// Install to .claude/skills/ (Claude Code)
copyFile(SKILL_SRC, path.join(CLAUDE_DIR, "SKILL.md"), "Skill (.claude/skills/)");

// Install CLI if --with-cli flag is passed
const withCli = process.argv.includes("--with-cli");
if (withCli) {
  const dest = path.join(process.cwd(), "subscribr.py");
  copyFile(CLI_SRC, dest, "CLI (subscribr.py)");
  try { fs.chmodSync(dest, 0o755); } catch (_) {}
}

console.log("\n  Setup:\n");
console.log("  1. Get an API token at https://subscribr.ai/developer");
console.log("  2. export SUBSCRIBR_API_TOKEN=sk_live_...\n");

if (withCli) {
  console.log("  CLI usage:\n");
  console.log("  python3 subscribr.py help");
  console.log("  python3 subscribr.py scripts create --channel_id 42 --title '...'\n");
} else {
  console.log("  To also install the Python CLI:");
  console.log("  npx @subscribr/cli --with-cli\n");
}

console.log("  Docs: https://subscribr.ai/youtube-api");
console.log("  API ref: curl -s https://subscribr.ai/api/docs/reference/ai\n");
