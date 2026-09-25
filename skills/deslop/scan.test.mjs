#!/usr/bin/env node
// Checks that scan.mjs reads a private per-clone scope from
// `<git common dir>/info/deslop-scope.json`, ahead of the two scopes a repository can commit
// to itself. Git never tracks that path, so the only way to exercise it is to build a real
// git repository (and a linked worktree, to prove --git-common-dir resolution) under a temp
// directory and write the file there directly.
//
//   node scan.test.mjs
//
// Exits 1 on any failure, so it also works as a pre-commit gate.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const scan = path.join(here, "scan.mjs");

let failed = 0;
let total = 0;
const check = (ok, text) => {
  total++;
  console.log((ok ? "pass  " : "FAIL  ") + text);
  if (!ok) failed++;
};

function git(args, cwd) {
  execFileSync("git", args, { cwd, stdio: ["ignore", "ignore", "inherit"] });
}

// scan.mjs exits 1 under --json when it finds a hit. That exit is a result to read, not a crash.
function runScan(cwd, extraArgs = []) {
  try {
    const out = execFileSync(process.execPath, [scan, "--json", ...extraArgs], { cwd, encoding: "utf8" });
    return JSON.parse(out);
  } catch (error) {
    if (!error.status || typeof error.stdout !== "string") throw error;
    return JSON.parse(error.stdout);
  }
}

const root = fs.mkdtempSync(path.join(os.tmpdir(), "deslop-scan-test-"));
const main = path.join(root, "main");
try {
  fs.mkdirSync(main);
  git(["init", "-q", "-b", "main"], main);
  git(["config", "user.email", "test@example.com"], main);
  git(["config", "user.name", "Test"], main);

  // A committed baseline, with no docRoots declared anywhere. The default scan reads no prose
  // file, which is the behavior a private scope must override to be provable at all.
  fs.writeFileSync(path.join(main, "README.md"), "Nothing to report here.\n");
  git(["add", "README.md"], main);
  git(["commit", "-q", "-m", "init"], main);

  const baseline = runScan(main);
  check(baseline.hits.length === 0, "no private scope: the plain README is not scanned");

  // The private scope lives outside every tracked tree, at .git/info, which git never adds to
  // an index and never ships. docRoots: [""] asks for the whole repository's prose.
  fs.mkdirSync(path.join(main, ".git", "info"), { recursive: true });
  fs.writeFileSync(
    path.join(main, ".git", "info", "deslop-scope.json"),
    JSON.stringify({ docRoots: [""] })
  );

  fs.writeFileSync(path.join(main, "README.md"), "This seamlessly does the thing.\n");
  git(["add", "README.md"], main);

  const withScope = runScan(main);
  const hit = withScope.hits.find(h => h.rule === "prose-tell" && h.file === "README.md");
  check(!!hit, "private scope read: README.md is scanned and the prose-tell hit is reported");
  check(hit && hit.match.toLowerCase() === "seamlessly", "the reported match is the planted word");

  // A linked worktree has its own .git (a pointer file), not its own .git/info. Resolving through
  // --git-common-dir is what makes the main clone's private scope reach a worktree at all.
  const worktree = path.join(root, "wt");
  git(["worktree", "add", "-q", worktree, "-b", "wt-branch"], main);
  fs.writeFileSync(path.join(worktree, "README.md"), "This also seamlessly does the thing.\n");
  git(["add", "README.md"], worktree);

  const fromWorktree = runScan(worktree);
  const wtHit = fromWorktree.hits.find(h => h.rule === "prose-tell" && h.file === "README.md");
  check(!!wtHit, "linked worktree: the main clone's private scope is still found and read");

  // A committed config and a CLAUDE.md deslop-scope block both declare docRoots, so without a
  // private scope either one would let the scan read README.md too. The private scope answers
  // first, and its docRoots is what the run above already used and already proved.
  fs.mkdirSync(path.join(main, ".claude"), { recursive: true });
  fs.writeFileSync(
    path.join(main, ".claude", "deslop.config.mjs"),
    "export default { docRoots: [] };\n"
  );
  fs.writeFileSync(
    path.join(main, "CLAUDE.md"),
    "```json deslop-scope\n{ \"docRoots\": [] }\n```\n"
  );
  const overCommitted = runScan(main);
  const overHit = overCommitted.hits.find(h => h.rule === "prose-tell" && h.file === "README.md");
  check(!!overHit, "private scope wins over a committed config and a CLAUDE.md block, both declaring no docRoots");

  // --config names a file directly, and that must win even over the private scope that would
  // otherwise answer first.
  const explicitConfigPath = path.join(root, "explicit.config.mjs");
  fs.writeFileSync(explicitConfigPath, "export default { docRoots: [] };\n");
  const explicit = runScan(main, ["--config", explicitConfigPath]);
  check(
    !explicit.hits.some(h => h.file === "README.md"),
    "--config wins over the private scope: docRoots: [] leaves README.md unread"
  );

  // --root points the scan at a second clone with its own private scope, so a scan run from
  // elsewhere must read that clone's file and not the one beside the caller's own .git.
  const other = path.join(root, "other");
  fs.mkdirSync(other);
  git(["init", "-q", "-b", "main"], other);
  git(["config", "user.email", "test@example.com"], other);
  git(["config", "user.name", "Test"], other);
  fs.writeFileSync(path.join(other, "README.md"), "This seamlessly does the thing too.\n");
  git(["add", "README.md"], other);
  git(["commit", "-q", "-m", "init"], other);
  fs.mkdirSync(path.join(other, ".git", "info"), { recursive: true });
  fs.writeFileSync(
    path.join(other, ".git", "info", "deslop-scope.json"),
    JSON.stringify({ docRoots: [""] })
  );
  const fromRoot = runScan(root, ["--root", other]);
  const rootHit = fromRoot.hits.find(h => h.rule === "prose-tell" && h.file === "README.md");
  check(!!rootHit, "--root from another clone reads the target's own private scope, not the caller's");

  // A malformed private scope must stop the scan rather than silently fall through to a
  // committed config or a CLAUDE.md block.
  const badRoot = path.join(root, "bad");
  fs.mkdirSync(badRoot);
  git(["init", "-q", "-b", "main"], badRoot);
  git(["config", "user.email", "test@example.com"], badRoot);
  git(["config", "user.name", "Test"], badRoot);
  fs.writeFileSync(path.join(badRoot, "README.md"), "Nothing to report here.\n");
  git(["add", "README.md"], badRoot);
  git(["commit", "-q", "-m", "init"], badRoot);
  fs.mkdirSync(path.join(badRoot, ".git", "info"), { recursive: true });
  fs.writeFileSync(path.join(badRoot, ".git", "info", "deslop-scope.json"), "{ not json");
  let badStatus = 0;
  try {
    execFileSync(process.execPath, [scan, "--json"], { cwd: badRoot, stdio: ["ignore", "ignore", "ignore"] });
  } catch (error) {
    badStatus = error.status;
  }
  check(badStatus === 2, "malformed private scope JSON exits 2");

  // A private scope file saved with a byte-order mark must still parse. Windows editors add one
  // by default, and a scope file is exactly the kind of file someone edits by hand.
  const bomRoot = path.join(root, "bom");
  fs.mkdirSync(bomRoot);
  git(["init", "-q", "-b", "main"], bomRoot);
  git(["config", "user.email", "test@example.com"], bomRoot);
  git(["config", "user.name", "Test"], bomRoot);
  fs.writeFileSync(path.join(bomRoot, "README.md"), "This seamlessly does the thing.\n");
  git(["add", "README.md"], bomRoot);
  git(["commit", "-q", "-m", "init"], bomRoot);
  fs.mkdirSync(path.join(bomRoot, ".git", "info"), { recursive: true });
  fs.writeFileSync(
    path.join(bomRoot, ".git", "info", "deslop-scope.json"),
    "﻿" + JSON.stringify({ docRoots: [""] })
  );
  const bomResult = runScan(bomRoot);
  const bomHit = bomResult.hits.find(h => h.rule === "prose-tell" && h.file === "README.md");
  check(!!bomHit, "a private scope file with a leading byte-order mark still parses");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

console.log("\n" + failed + " failed, " + total + " checks");
process.exit(failed ? 1 : 0);
