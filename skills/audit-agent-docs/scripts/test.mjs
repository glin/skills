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

import { execFileSync, spawnSync } from "node:child_process";
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
  ["superseded-state", "skill:superseded-asserted", "skill:superseded-quoted", "the old name is quoted"],
  ["superseded-state", "skill:used-to-asserted", "skill:used-to-passive", "used to is a passive, not an earlier state"],
  ["hand-maintained-sync", "skill:sync-asserted", "skill:sync-quoted", "the phrase is quoted"],
  [
    "superseded-state",
    "claude.md:claude/superseded.md",
    "claude.md:claude/current.md",
    "the old name is gone",
  ],
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
  [
    "dated-provenance",
    "claude.md:claude/dated-provenance.md",
    "claude.md:claude/dated-quoted.md",
    "the date stamp is quoted",
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

// Exercise CLI selection with one valid skill and one valid instruction file.
// Each case asserts the exit status and the number of files actually audited.
const selection = fs.mkdtempSync(path.join(os.tmpdir(), "audit-selection-"));
try {
  const parent = path.join(selection, "skills");
  const direct = path.join(parent, "forward-path");
  const empty = path.join(selection, "empty");
  const missing = path.join(selection, "missing");
  const claude = path.join(selection, "CLAUDE.md");
  fs.mkdirSync(direct, { recursive: true });
  fs.mkdirSync(empty);
  fs.copyFileSync(path.join(skillFixtures, "forward-path", "fixture.md"), path.join(direct, "SKILL.md"));
  fs.copyFileSync(path.join(claudeDir, "undated.md"), claude);
  const cases = [
    ["direct skill", ["skills", direct], 0, 1, 0],
    ["parent directory", ["skills", parent], 0, 1, 0],
    ["instruction file", ["claude", claude], 0, 0, 1],
    ["both categories", ["both", "--skills", direct, "--claude", claude], 0, 1, 1],
    ["omitted skills category", ["both", "--claude", claude], 0, 0, 1],
    ["omitted claude category", ["both", "--skills", parent], 0, 1, 0],
    ["empty skills category", ["skills"], 1, 0, 0],
    ["empty claude category", ["claude"], 1, 0, 0],
    ["no categories", ["both"], 1, 0, 0],
    ["missing skill path", ["skills", missing], 1, 0, 0],
    ["empty skill directory", ["skills", empty], 1, 0, 0],
    ["file as skill directory", ["skills", claude], 1, 0, 0],
    ["missing instruction file", ["claude", missing], 1, 0, 0],
    ["directory as instruction file", ["claude", empty], 1, 0, 0],
    ["missing skills with valid claude", ["both", "--skills", missing, "--claude", claude], 1, 0, 1],
    ["missing claude with valid skills", ["both", "--skills", direct, "--claude", missing], 1, 1, 0],
    ["empty skills with valid claude", ["both", "--skills", "--claude", claude], 1, 0, 1],
    ["empty claude with valid skills", ["both", "--claude", "--skills", direct], 1, 1, 0],
    ["path before any category", ["both", direct, "--claude", claude], 1, 0, 1],
    ["parent and its child", ["skills", parent, direct], 0, 1, 0],
    ["missing input alongside valid skill", ["skills", parent, missing], 1, 1, 0],
    ["empty directory alongside valid skill", ["skills", parent, empty], 1, 1, 0],
    ["valid repository root", ["both", "--root", selection, "--skills", direct], 0, 1, 0],
    ["trailing root", ["both", "--claude", claude, "--root"], 2],
    ["root before skills flag", ["both", "--root", "--skills", "--claude", claude], 2],
    ["empty root value", ["skills", direct, "--root", ""], 2],
  ];
  for (const [name, args, status, skills, docs] of cases) {
    const result = spawnSync(process.execPath, [path.join(here, "audit.mjs"), ...args], { encoding: "utf8" });
    if (result.error) throw result.error;
    check(result.status === status, name + ": exit " + status + " (received " + result.status + ")");
    if (status === 2) {
      check(result.stderr.includes("--root requires a repository path"), name + ": explains the missing root value");
    } else {
      check(result.stdout.includes(`Audited ${skills} skill targets and ${docs} CLAUDE.md targets.`), name + ": reports audited targets");
      if (skills) check((result.stdout.match(/skill:forward-path\s+size\s/g) || []).length === 1, name + ": read the skill file once");
      if (docs) check(/claude\.md:.*CLAUDE\.md\s+size\s/.test(result.stdout), name + ": read the instruction file");
    }
  }
  // The skill name comes from the resolved directory, so "." inside a skill still matches its name.
  const inside = spawnSync(process.execPath, [path.join(here, "audit.mjs"), "skills", "."], { encoding: "utf8", cwd: direct });
  check(inside.status === 0 && !/name-mismatch/.test(inside.stdout), "current skill directory: exit 0 with no name mismatch");
} finally {
  fs.rmSync(selection, { recursive: true, force: true });
}

// Every phrase that a regex body of words, | and (...) groups can match.
function expand(src) {
  const alts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < src.length; i++) {
    if (src[i] === "(") depth++;
    else if (src[i] === ")") depth--;
    else if (src[i] === "|" && depth === 0) {
      alts.push(src.slice(start, i));
      start = i + 1;
    }
  }
  alts.push(src.slice(start));
  return alts.flatMap((alt) => {
    const open = alt.indexOf("(");
    if (open < 0) return [alt];
    let close = open;
    for (let d = 0; close < alt.length; close++) {
      if (alt[close] === "(") d++;
      else if (alt[close] === ")" && --d === 0) break;
    }
    const head = alt.slice(0, open);
    const tails = expand(alt.slice(close + 1));
    return expand(alt.slice(open + 1, close)).flatMap((mid) => tails.map((tail) => head + mid + tail));
  });
}

// The earlier-state phrases extend the deslop superseded rule. Each phrase that
// rule matches must also trip superseded-state, or the two lists have drifted.
const deslopSkill = path.join(path.dirname(skill), "deslop", "SKILL.md");
if (fs.existsSync(deslopSkill)) {
  const block = fs.readFileSync(deslopSkill, "utf8").match(/```json deslop-patterns\r?\n([\s\S]*?)```/);
  const phrases = expand(JSON.parse(block[1]).rules.superseded.replace(/^\\b\(/, "").replace(/\)\\b$/, ""));
  const plain = phrases.every((ph) => /^[a-z' ]+$/i.test(ph));
  check(plain, "deslop superseded phrases are plain words: " + phrases.join(", "));
  const shared = fs.mkdtempSync(path.join(os.tmpdir(), "audit-shared-"));
  try {
    phrases.forEach((ph, i) => {
      fs.mkdirSync(path.join(shared, "phrase-" + i));
      fs.writeFileSync(
        path.join(shared, "phrase-" + i, "SKILL.md"),
        `---\nname: phrase-${i}\ndescription: Fixture for one deslop phrase. Use when the test harness runs, never in real work.\n---\n\n# Phrase\n\nThe parser ${ph} the cache.\n`
      );
    });
    const r = spawnSync(process.execPath, [path.join(here, "audit.mjs"), "skills", shared], { encoding: "utf8" });
    phrases.forEach((ph, i) =>
      check(new RegExp(`skill:phrase-${i}\\s+superseded-state`).test(r.stdout), `superseded-state: catches the deslop phrase "${ph}"`)
    );
  } finally {
    fs.rmSync(shared, { recursive: true, force: true });
  }
} else {
  console.log("skip  deslop is not beside this skill, so the shared phrase check did not run");
}

// The symbol scan follows a directory link, as the path index does.
const linked = fs.mkdtempSync(path.join(os.tmpdir(), "audit-linked-"));
try {
  const repo = path.join(linked, "repo");
  fs.mkdirSync(path.join(linked, "pkg"));
  fs.mkdirSync(repo);
  fs.writeFileSync(path.join(linked, "pkg", "checks.js"), "function CheckLinkedGuard() {}\n");
  fs.writeFileSync(path.join(repo, "CLAUDE.md"), "## Checks\n\n- Run CheckLinkedGuard before you commit.\n");
  const run = () =>
    spawnSync(process.execPath, [path.join(here, "audit.mjs"), "claude", path.join(repo, "CLAUDE.md"), "--root", repo], { encoding: "utf8" }).stdout;
  check(!/restates-a-guard/.test(run()), "restates-a-guard: silent when no link reaches the symbol");
  fs.symlinkSync(path.join(linked, "pkg"), path.join(repo, "linked"), "junction");
  check(/restates-a-guard.*CheckLinkedGuard/.test(run()), "restates-a-guard: reads a symbol behind a directory link");
} finally {
  fs.rmSync(linked, { recursive: true, force: true });
}

console.log("\n" + failed + " failed, " + total + " checks");
process.exit(failed ? 1 : 0);
