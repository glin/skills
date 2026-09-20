#!/usr/bin/env node
// Checks that audit.mjs still reports what it is meant to report, and stays
// silent on the twin that differs only in the thing being guarded.
//
//   node scripts/test.mjs
//
// Every case is a pair. The red fixture must produce the rule, and the green
// fixture must not. A pair whose halves differ in anything except the guarded
// property proves nothing, so keep the wording of the twins identical apart
// from that one difference.
//
// Exits 1 on any failure, so it also works as a pre-commit gate.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const skill = path.dirname(here);
const skillFixtures = path.join(skill, "fixtures", "skills");
const claudeDir = path.join(skill, "fixtures", "claude");

// The rule, the fixture that must trip it, the twin that must not, and what the
// two differ by. That last field is why the pair is evidence rather than two
// unrelated files.
const PAIRS = [
  ["shouting", "skill:shouting-asserted", "skill:shouting-quoted", "the capitals are quoted"],
  ["time-sensitive", "skill:date-asserted", "skill:date-quoted", "the date is quoted"],
  ["windows-path", "skill:backslash-path", "skill:forward-path", "the separator is a forward slash"],
  ["machine-path", "skill:drive-path", "skill:skill-relative-path", "the script is named from $SKILL_DIR"],
  ["machine-path", "skill:user-home-path", "skill:skill-relative-path", "the script is named from $SKILL_DIR"],
  ["machine-path", "skill:home-folder-path", "skill:skill-relative-path", "the script is named from $SKILL_DIR"],
  ["machine-path", "skill:installed-skill-path", "skill:skill-relative-path", "the script is named from $SKILL_DIR"],
  ["machine-path", "skill:placeholder-path", "skill:skill-relative-path", "the script is named from $SKILL_DIR"],
  ["machine-path", "skill:sibling-parent-path", "skill:skill-relative-path", "the script is named from $SKILL_DIR"],
  ["readme-in-skill", "skill:readme-unnamed", "skill:readme-named", "SKILL.md names the README"],
  [
    "pointer-with-retelling",
    "claude.md:claude/pointer-retells.md",
    "claude.md:claude/pointer-split.md",
    "the rule and the pointer are separate bullets",
  ],
  [
    "long-parenthetical",
    "claude.md:claude/long-parenthetical.md",
    "claude.md:claude/short-parenthetical.md",
    "the aside is shorter",
  ],
  [
    "dated-provenance",
    "claude.md:claude/dated-provenance.md",
    "claude.md:claude/undated.md",
    "the date stamp is gone",
  ],
];

const claudeFiles = fs
  .readdirSync(claudeDir)
  .filter((f) => f.endsWith(".md"))
  .map((f) => path.join(claudeDir, f));

// Codex lists every SKILL.md inside an installed skill as a skill of its own, so
// each fixture is stored as fixture.md and staged as SKILL.md only for this run.
const staged = fs.mkdtempSync(path.join(os.tmpdir(), "audit-fixtures-"));
for (const name of fs.readdirSync(skillFixtures)) {
  const source = path.join(skillFixtures, name, "fixture.md");
  if (!fs.existsSync(source)) continue;
  fs.mkdirSync(path.join(staged, name));
  fs.copyFileSync(source, path.join(staged, name, "SKILL.md"));
  // A fixture may bundle another file, such as the README that one rule reads.
  for (const extra of fs.readdirSync(path.join(skillFixtures, name)))
    if (extra !== "fixture.md")
      fs.copyFileSync(path.join(skillFixtures, name, extra), path.join(staged, name, extra));
}

// audit.mjs exits 1 when a fixture trips an error-level rule. That exit is a
// result to read here, not a crash.
let out;
try {
  out = execFileSync(
    process.execPath,
    [path.join(here, "audit.mjs"), "both", "--skills", staged, "--claude", ...claudeFiles],
    { encoding: "utf8" }
  );
} catch (error) {
  if (!error.status || typeof error.stdout !== "string") throw error;
  out = error.stdout;
} finally {
  fs.rmSync(staged, { recursive: true, force: true });
}

// audit.mjs pads its columns, but neither the target nor the rule contains a
// space, so the first two tokens are enough and the widths do not have to be
// reproduced here.
const found = new Set();
for (const line of out.split(/\r?\n/)) {
  if (!line.trim() || line.startsWith("==") || /^\d+ errors/.test(line)) continue;
  const [target, rule] = line.trim().split(/\s+/);
  if (target && rule) found.add(target + " " + rule);
}

let failed = 0;
let total = 0;
const check = (ok, text) => {
  total++;
  console.log((ok ? "pass  " : "FAIL  ") + text);
  if (!ok) failed++;
};

for (const [rule, red, green, differs] of PAIRS) {
  check(found.has(red + " " + rule), rule + ": reported on " + red);
  check(!found.has(green + " " + rule), rule + ": silent on " + green + ", where " + differs);
}

// A fixture the auditor stopped reading would pass every "silent" assertion
// above while proving nothing. Every audited target emits a size stat, so its
// presence is what separates silence from absence.
const seen = new Set([...found].map((k) => k.split(" ")[0]));
for (const [, red, green] of PAIRS) {
  for (const target of [red, green]) check(seen.has(target), target + " was audited");
}

console.log("\n" + failed + " failed, " + total + " checks");
process.exit(failed ? 1 : 0);
