#!/usr/bin/env node
// Mechanical checks for SKILL.md files and CLAUDE.md files.
// Reports only what a script can decide; judgement calls stay with the reader.
//
//   node audit.mjs skills <dir>...    audit every skill directory under each <dir>
//   node audit.mjs claude <file>...   audit each CLAUDE.md
//   node audit.mjs both  --skills <dir>... --claude <file>...

import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const KNOWN_FRONTMATTER = new Set([
  "name",
  "description",
  "argument-hint",
  "allowed-tools",
  "disable-model-invocation",
  "license",
  "compatibility",
  "metadata",
  "model",
]);

const MAX_NAME = 64;
const MAX_DESCRIPTION = 1024;
const MAX_BODY_LINES = 500;
const MAX_BODY_WORDS = 5000;
const TOC_REQUIRED_OVER_LINES = 100;
const LONG_PARENTHETICAL_WORDS = 25;
const SHINGLE_SIZE = 6;
const NEAR_DUPLICATE_RATIO = 0.5;

// Repo scanning, for the stale-reference and duplicated-guard checks.
//
// What to skip comes from the repo's own ignore rules rather than a list here,
// since every project names its build output differently. The fallback applies
// only when the tree is not under version control.
const FALLBACK_SKIP_DIRS = new Set([
  "node_modules", ".git", "dist", "build", "target", "vendor", "__pycache__",
]);

// Formats that declare no identifiers but would consume the read budget. This
// is a denylist, not an allowlist, so a source extension nobody thought to add
// is still read rather than silently passed over.
const NON_SOURCE_EXT = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf", ".mp3",
  ".mp4", ".mov", ".wav", ".woff", ".woff2", ".ttf", ".otf", ".zip", ".gz",
  ".tar", ".exe", ".dll", ".so", ".dylib", ".bin", ".wasm", ".pyc", ".class",
  ".jar", ".lock", ".csv", ".tsv", ".map",
]);
const MAX_SCAN_BYTES = 24 * 1024 * 1024;
const GUARD_NAME = /^[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+$/;

const findings = [];

function report(level, target, rule, detail) {
  findings.push({ level, target, rule, detail });
}

function splitFrontmatter(text) {
  const lines = text.split(/\r?\n/);
  if (lines[0] !== "---") return { frontmatter: null, body: text, bodyOffset: 0 };
  const end = lines.indexOf("---", 1);
  if (end === -1) return { frontmatter: null, body: text, bodyOffset: 0 };
  return {
    frontmatter: lines.slice(1, end),
    body: lines.slice(end + 1).join("\n"),
    bodyOffset: end + 1,
  };
}

// Enough YAML for the frontmatter shapes a SKILL.md uses: scalar values,
// quoted strings, and values continued on following indented lines.
function parseFrontmatter(lines) {
  const out = {};
  let key = null;
  for (const line of lines) {
    const m = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (m && !/^\s/.test(line)) {
      key = m[1];
      out[key] = m[2];
    } else if (key && /^\s+\S/.test(line)) {
      out[key] += " " + line.trim();
    }
  }
  for (const k of Object.keys(out)) {
    out[k] = out[k].trim().replace(/^["'](.*)["']$/s, "$1").trim();
  }
  return out;
}

function words(s) {
  return s.split(/\s+/).filter(Boolean).length;
}

function stripCode(text) {
  return text.replace(/```[\s\S]*?```/g, "").replace(/`[^`\n]*`/g, "");
}

function localLinks(text) {
  const out = new Set();
  for (const m of text.matchAll(/\[[^\]]*\]\(([^)]+)\)/g)) {
    const href = m[1].trim();
    if (/^[a-z]+:/i.test(href) || href.startsWith("#")) continue;
    // `![name](url)` and friends are placeholders in a template, not links.
    if (!/[./]/.test(href)) continue;
    out.add(href.split("#")[0]);
  }
  return [...out];
}

// A word in capitals is shouting only if the same word appears in lower case
// somewhere in the document. Acronyms and identifiers never do, so they pass
// without needing an allowlist to maintain.
function shoutedWords(prose) {
  const lower = new Set(prose.toLowerCase().match(/\b[a-z]{2,}\b/g) || []);
  const hits = [];
  for (const m of prose.matchAll(/\b[A-Z]{2,}\b(?!\.[a-z])/g)) {
    const w = m[0];
    const others = [...prose.matchAll(new RegExp(`\\b${w}\\b`, "g"))].length;
    const lowerCount = [...prose.matchAll(new RegExp(`\\b${w.toLowerCase()}\\b`, "g"))].length;
    if (lower.has(w.toLowerCase()) && lowerCount >= others) hits.push(w);
  }
  return hits;
}

function normalise(s) {
  return s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
}

function auditSkill(dir) {
  const name = path.basename(dir);
  const skillPath = path.join(dir, "SKILL.md");
  const t = `skill:${name}`;

  if (!fs.existsSync(skillPath)) {
    report("error", t, "missing-skill-md", `no SKILL.md in ${dir}`);
    return;
  }
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(name)) {
    report("error", t, "directory-name", "directory name is not kebab-case");
  }

  const text = fs.readFileSync(skillPath, "utf8");
  const { frontmatter, body } = splitFrontmatter(text);
  if (!frontmatter) {
    report("error", t, "frontmatter-missing", "no --- delimited YAML frontmatter");
    return;
  }
  const fm = parseFrontmatter(frontmatter);

  // name
  if (!fm.name) report("error", t, "name-missing", "no name field");
  else {
    if (fm.name !== name) report("error", t, "name-mismatch", `name "${fm.name}" != directory "${name}"`);
    if (fm.name.length > MAX_NAME) report("error", t, "name-too-long", `${fm.name.length} > ${MAX_NAME}`);
    if (!/^[a-z0-9-]+$/.test(fm.name)) report("error", t, "name-charset", "must be lowercase letters, numbers, hyphens");
    if (/claude|anthropic/i.test(fm.name)) report("error", t, "name-reserved", 'contains "claude" or "anthropic"');
  }

  // description
  const d = fm.description || "";
  if (!d) report("error", t, "description-missing", "no description field");
  else {
    if (d.length > MAX_DESCRIPTION) report("error", t, "description-too-long", `${d.length} > ${MAX_DESCRIPTION} chars`);
    if (/[<>]/.test(d)) report("error", t, "description-xml", "contains angle brackets");
    if (/^(I |I'|You |Your )/.test(d) || /\bI can help\b/i.test(d))
      report("warn", t, "description-person", "not written in third person");
    if (!/\b(use when|when the user|use this|whenever)\b/i.test(d))
      report("warn", t, "description-no-trigger", "states what it does but not when to use it");
    if (d.length < 80) report("warn", t, "description-thin", `${d.length} chars, likely too few trigger terms`);
  }

  for (const k of Object.keys(fm)) {
    if (!KNOWN_FRONTMATTER.has(k)) report("warn", t, "frontmatter-unknown", `unrecognised field "${k}"`);
  }

  // body size
  const bodyLines = body.split(/\r?\n/).length;
  if (bodyLines > MAX_BODY_LINES) report("error", t, "body-too-long", `${bodyLines} lines > ${MAX_BODY_LINES}`);
  else if (bodyLines > MAX_BODY_LINES * 0.8) report("warn", t, "body-near-limit", `${bodyLines} lines`);
  const bodyWords = words(stripCode(body));
  if (bodyWords > MAX_BODY_WORDS) report("warn", t, "body-wordy", `${bodyWords} words > ${MAX_BODY_WORDS}`);

  // anti-patterns in the body
  const prose = stripCode(body);
  const shouted = shoutedWords(prose);
  if (shouted.length) {
    const counted = [...new Set(shouted)].map((w) => `${w}(${shouted.filter((x) => x === w).length})`);
    report("warn", t, "shouting", `${shouted.length} words capitalised for emphasis: ${counted.join(" ")}`);
  }
  for (const m of body.matchAll(/`([^`\n]*\\[^`\n]*)`/g)) {
    const span = m[1];
    // Only skill-relative paths need forward slashes. Absolute Windows paths
    // and shell variables are the correct form for a Windows-only skill, and
    // a regex is not a path at all.
    if (/^[A-Za-z]:\\|\$env:|%[A-Z_]+%/.test(span)) continue;
    if (/\\[dwsbnrt.\-+*?()[\]{}|^$]/.test(span)) continue;
    if (/[A-Za-z0-9_.-]\\[A-Za-z0-9_.-]/.test(span))
      report("warn", t, "windows-path", `relative path uses backslashes: \`${span}\``);
  }
  for (const m of prose.matchAll(/\b(keep them identical|source of truth for the|bump .{0,20}quarterly|last reviewed)\b/gi)) {
    report("warn", t, "hand-maintained-sync", `"${m[0]}" makes currency depend on someone remembering`);
  }
  const dated = [...prose.matchAll(/\b(as of|currently|recently|new in|since version|20\d\d-\d\d-\d\d|confirmed 20\d\d)\b/gi)];
  if (dated.length) report("info", t, "time-sensitive", `${dated.length} time-sensitive phrases: ${[...new Set(dated.map((x) => x[0]))].join(", ")}`);

  if (fs.existsSync(path.join(dir, "README.md")))
    report("warn", t, "readme-in-skill", "README.md inside the skill folder");

  // progressive disclosure and reference depth
  const referenced = localLinks(body);
  for (const rel of referenced) {
    const target = path.resolve(dir, rel);
    if (!fs.existsSync(target)) {
      report("error", t, "dangling-link", `SKILL.md links to missing ${rel}`);
      continue;
    }
    if (!target.startsWith(path.resolve(dir))) continue;
    const sub = fs.readFileSync(target, "utf8");
    const subLines = sub.split(/\r?\n/).length;
    if (subLines > TOC_REQUIRED_OVER_LINES && !/^##\s*(contents|table of contents)/im.test(sub))
      report("warn", t, "reference-no-toc", `${rel} is ${subLines} lines with no contents list`);
    for (const deep of localLinks(sub)) {
      const deepTarget = path.resolve(path.dirname(target), deep);
      if (deepTarget.startsWith(path.resolve(dir)) && deepTarget !== path.resolve(skillPath))
        report("warn", t, "reference-depth", `${rel} links onward to ${deep}, two hops from SKILL.md`);
    }
  }

  // files present but never pointed at
  const bundled = [];
  (function walk(p) {
    for (const e of fs.readdirSync(p, { withFileTypes: true })) {
      const full = path.join(p, e.name);
      if (e.isDirectory()) walk(full);
      else if (full !== skillPath) bundled.push(path.relative(dir, full).replace(/\\/g, "/"));
    }
  })(dir);
  for (const f of bundled) {
    if (f.endsWith(".meta")) continue;
    // A skill that names a directory (`entries/`) has pointed at everything in it.
    const dirNamed = f.includes("/") && body.includes(f.slice(0, f.lastIndexOf("/") + 1));
    if (!dirNamed && !body.includes(f) && !body.includes(path.basename(f)))
      report("info", t, "unreferenced-file", `${f} is bundled but never named in SKILL.md`);
  }

  skillBodies.push({ name, prose: normalise(stripCode(body)) });
  report("stat", t, "size", `${bodyLines} lines, ${bodyWords} words, description ${d.length} chars, ${bundled.length} bundled files`);
}

// Two skills carrying the same block of text have two places to update and one
// that will be forgotten. Report the longest run of identical wording per pair.
const skillBodies = [];
const DUPLICATE_RUN_WORDS = 25;

function crossSkillDuplicates() {
  for (let a = 0; a < skillBodies.length; a++) {
    for (let b = a + 1; b < skillBodies.length; b++) {
      const wa = skillBodies[a].prose.split(" ");
      const wb = new Set();
      const wbWords = skillBodies[b].prose.split(" ");
      for (let i = 0; i + DUPLICATE_RUN_WORDS <= wbWords.length; i++)
        wb.add(wbWords.slice(i, i + DUPLICATE_RUN_WORDS).join(" "));
      let longest = "";
      for (let i = 0; i + DUPLICATE_RUN_WORDS <= wa.length; i++) {
        const run = wa.slice(i, i + DUPLICATE_RUN_WORDS).join(" ");
        if (!wb.has(run)) continue;
        let end = i + DUPLICATE_RUN_WORDS;
        while (end < wa.length && wb.has(wa.slice(end - DUPLICATE_RUN_WORDS + 1, end + 1).join(" "))) end++;
        const full = wa.slice(i, end).join(" ");
        if (full.length > longest.length) longest = full;
      }
      if (longest)
        report(
          "warn",
          `skill:${skillBodies[a].name} + ${skillBodies[b].name}`,
          "duplicated-across-skills",
          `${longest.split(" ").length} identical words: "${longest.slice(0, 80)}..."`
        );
    }
  }
}

// Paths the repo's own ignore rules already exclude, so build output and caches
// are skipped without this script guessing what a project calls them. Null when
// the tree is not a checkout, which is what sends the walk to the fallback.
function ignoredPaths(root) {
  try {
    const out = execFileSync(
      "git",
      ["-C", root, "ls-files", "-z", "--directory", "--others", "--ignored", "--exclude-standard"],
      { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "ignore"] }
    );
    return new Set(out.split("\0").filter(Boolean).map((p) => p.replace(/\/$/, "")));
  } catch {
    return null;
  }
}

// Every identifier defined anywhere in the repo's source, read once and capped
// so a large repository cannot stall the audit.
let repoIndex = null;

function indexRepo(root) {
  if (repoIndex && repoIndex.root === root) return repoIndex;
  const names = new Set();
  const basenames = new Set();
  const paths = new Set();
  const exts = new Set();
  let bytes = 0;
  const ignored = ignoredPaths(root);
  const skipDir = (rel, name) =>
    name === ".git" || (ignored ? ignored.has(rel) : FALLBACK_SKIP_DIRS.has(name));
  // Names first, for every file and directory. Cheap, and it is what the
  // stale-reference check needs; only source files get read for identifiers.
  // Junctions and symlinks are followed, because linked-in packages are where a
  // lot of referenced docs live. The realpath set stops a link loop.
  const visited = new Set();
  (function walkNames(dir) {
    let real;
    try {
      real = fs.realpathSync(dir);
    } catch {
      return;
    }
    if (visited.has(real)) return;
    visited.add(real);
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      const rel = path.relative(root, full).replace(/\\/g, "/");
      let isDir = e.isDirectory();
      if (!isDir && e.isSymbolicLink()) {
        try {
          isDir = fs.statSync(full).isDirectory();
        } catch {
          isDir = false;
        }
      }
      if (isDir) {
        if (skipDir(rel, e.name)) continue;
        paths.add(rel + "/");
        basenames.add(e.name + "/");
        walkNames(full);
      } else {
        if (ignored && ignored.has(rel)) continue;
        paths.add(rel);
        basenames.add(e.name);
        const ext = path.extname(e.name).slice(1).toLowerCase();
        if (ext) exts.add(ext);
      }
    }
  })(root);
  (function walk(dir) {
    if (bytes > MAX_SCAN_BYTES) return;
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (bytes > MAX_SCAN_BYTES) return;
      const full = path.join(dir, e.name);
      const rel = path.relative(root, full).replace(/\\/g, "/");
      if (e.isDirectory()) {
        if (!skipDir(rel, e.name)) walk(full);
        continue;
      }
      if (ignored && ignored.has(rel)) continue;
      if (NON_SOURCE_EXT.has(path.extname(e.name).toLowerCase())) continue;
      let content;
      try {
        const st = fs.statSync(full);
        if (st.size > 2 * 1024 * 1024) continue;
        bytes += st.size;
        content = fs.readFileSync(full, "utf8");
      } catch {
        continue;
      }
      // Anything still binary at this point declares nothing worth indexing.
      if (content.includes("\0")) continue;
      names.add(path.basename(e.name, path.extname(e.name)));
      for (const m of content.matchAll(
        /\b(?:class|interface|struct|def|func|function|type|enum)\s+([A-Za-z_][A-Za-z0-9_]*)/g
      ))
        names.add(m[1]);
    }
  })(root);
  repoIndex = { root, names, basenames, paths, exts, truncated: bytes > MAX_SCAN_BYTES };
  return repoIndex;
}

// Extensions a reference can plausibly end in. Without this, property accesses
// (`array.length`) and dotted identifiers (`process.env`) read as filenames.
// Every extension the repo itself uses counts too, so a project built on file
// types absent from this baseline still has its references checked.
const REF_EXT = new Set([
  "md", "cs", "ts", "tsx", "js", "mjs", "jsx", "py", "rs", "go", "java", "rb",
  "sh", "ps1", "cmd", "bat", "json", "yml", "yaml", "toml", "xml", "html",
  "css", "sql", "txt", "cfg", "ini", "lock", "png", "jpg", "zip", "mp4", "csv",
]);

// A rule that names a validator, test, or command the repo actually runs should
// be one line pointing at it. The prose restates what the build already enforces.
function checkGuardDuplication(t, prose, root) {
  const idx = indexRepo(root);
  const seen = new Set();
  for (const m of prose.matchAll(/\b([A-Z][A-Za-z0-9]{4,})\b/g)) {
    const name = m[1];
    if (seen.has(name) || !GUARD_NAME.test(name)) continue;
    seen.add(name);
    if (idx.names.has(name))
      report("info", t, "restates-a-guard", `names ${name}, which the repo defines; a pointer may replace the prose`);
  }
  if (idx.truncated) report("info", t, "scan-truncated", "repo scan hit its size cap, guard check is partial");
}

// Anything the file asserts about the filesystem can rot without any edit to the
// file itself, so it is checked against the filesystem rather than trusted.
function checkStaleReferences(t, text, root, selfPath) {
  const idx = indexRepo(root);
  const seen = new Set();
  for (const m of text.matchAll(/`([^`\n]+)`/g)) {
    const ref = m[1].trim().split(/[:\s]/)[0].replace(/[.,;)]$/, "");
    if (seen.has(ref) || ref.length < 3) continue;
    seen.add(ref);
    if (/[*?<>|{}(),$%]/.test(ref) || /^[~.\-]/.test(ref) || /^[a-z]+:\/\//i.test(ref)) continue;

    const ext = (ref.match(/\.([A-Za-z0-9]+)$/) || [])[1];
    const isDir = ref.endsWith("/");
    const known = ext && (REF_EXT.has(ext.toLowerCase()) || idx.exts.has(ext.toLowerCase()));
    if (!isDir && !known) continue;

    // Resolve as written, then as a repo-relative path, then by basename, so a
    // file named without its directory still counts as present.
    if (fs.existsSync(path.resolve(root, ref)) || fs.existsSync(path.resolve(path.dirname(selfPath), ref))) continue;
    if (idx.paths.has(ref) || idx.basenames.has(ref)) continue;
    if ([...idx.paths].some((p) => p.endsWith("/" + ref))) continue;

    report("warn", t, "stale-reference", `\`${ref}\` is named but no such file exists in ${path.basename(root)}`);
  }
}

function auditClaudeMd(file, root) {
  const t = `claude.md:${file.replace(/\\/g, "/").split("/").slice(-2).join("/")}`;
  const text = fs.readFileSync(file, "utf8");
  const lines = text.split(/\r?\n/);
  const prose = stripCode(text);

  report("stat", t, "size", `${lines.length} lines, ${words(prose)} words`);

  // Instruction budget: bullets and standalone directive paragraphs.
  const bullets = lines.filter((l) => /^\s*[-*]\s+\S/.test(l)).length;
  const paragraphs = prose.split(/\n\s*\n/).filter((p) => p.trim() && !/^#/.test(p.trim())).length;
  report("stat", t, "instruction-count", `${bullets} bullets, ${paragraphs} non-heading paragraphs`);

  // Where the mass actually is. Choose what to audit from this, not from the
  // section that happens to be top of mind.
  const sections = [];
  let currentSection = { title: "(preamble)", start: 1, lines: [] };
  lines.forEach((l, i) => {
    if (/^##\s+\S/.test(l)) {
      sections.push(currentSection);
      currentSection = { title: l.replace(/^#+\s*/, ""), start: i + 1, lines: [] };
    } else currentSection.lines.push(l);
  });
  sections.push(currentSection);
  const total = words(prose) || 1;
  for (const s of sections) {
    const w = words(stripCode(s.lines.join("\n")));
    if (!w) continue;
    const share = Math.round((w / total) * 100);
    report("stat", t, "section", `${String(w).padStart(5)} words (${String(share).padStart(2)}%)  line ${s.start}  ${s.title}`);
  }

  if (root) {
    checkStaleReferences(t, text, root, file);
    checkGuardDuplication(t, prose, root);
  }

  // Long parentheticals: the specific defect that turns a rule into a case study.
  for (const m of prose.matchAll(/\(([^()]{40,})\)/g)) {
    const w = words(m[1]);
    if (w > LONG_PARENTHETICAL_WORDS) {
      const line = text.slice(0, m.index).split(/\r?\n/).length;
      report("warn", t, "long-parenthetical", `line ${line}: ${w}-word parenthetical: "${m[1].slice(0, 70)}..."`);
    }
  }

  // Dated provenance: fine in a ledger, noise in a rule.
  for (const m of prose.matchAll(/\(?(confirmed|verified|fixed|added|review(?:ed)?|as of)\s+20\d\d-\d\d(-\d\d)?\)?/gi)) {
    const line = text.slice(0, m.index).split(/\r?\n/).length;
    report("warn", t, "dated-provenance", `line ${line}: "${m[0]}"`);
  }

  // A pointer next to a retelling is the real defect; a pointer alone is the fix.
  for (const l of lines) {
    if (!/^\s*[-*]\s+\S/.test(l)) continue;
    const hasPointer = /`[^`]*\.(md|ts|js|py|cs|json)`|\]\([^)]+\.(md|ts|js|py|cs)\)/.test(l);
    if (hasPointer && words(stripCode(l)) > 45) {
      const line = lines.indexOf(l) + 1;
      report("warn", t, "pointer-with-retelling", `line ${line}: ${words(stripCode(l))}-word bullet that also points at a doc`);
    }
  }

  // Near-duplicate bullets.
  const bulletTexts = lines
    .map((l, i) => ({ i: i + 1, s: stripCode(l).replace(/^\s*[-*]\s+/, "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim() }))
    .filter((b) => /^\s*[-*]\s+\S/.test(lines[b.i - 1]) && words(b.s) >= SHINGLE_SIZE * 2);
  const shingles = bulletTexts.map((b) => {
    const w = b.s.split(" ");
    const set = new Set();
    for (let i = 0; i + SHINGLE_SIZE <= w.length; i++) set.add(w.slice(i, i + SHINGLE_SIZE).join(" "));
    return { ...b, set };
  });
  for (let a = 0; a < shingles.length; a++) {
    for (let b = a + 1; b < shingles.length; b++) {
      const inter = [...shingles[a].set].filter((s) => shingles[b].set.has(s)).length;
      const denom = Math.min(shingles[a].set.size, shingles[b].set.size) || 1;
      if (inter / denom >= NEAR_DUPLICATE_RATIO)
        report("warn", t, "near-duplicate", `lines ${shingles[a].i} and ${shingles[b].i} share ${Math.round((inter / denom) * 100)}% of their phrasing`);
    }
  }

  // Pasted code where a file:line reference would do.
  for (const m of text.matchAll(/```[\s\S]*?```/g)) {
    const n = m[0].split(/\r?\n/).length;
    if (n > 12) {
      const line = text.slice(0, m.index).split(/\r?\n/).length;
      report("info", t, "long-code-block", `line ${line}: ${n}-line code block, consider a file:line reference`);
    }
  }
}

function main() {
  const argv = process.argv.slice(2);
  const mode = argv[0];
  const rest = argv.slice(1);

  const skillRoots = [];
  const claudeFiles = [];
  let repoRoot = null;
  const takeRoot = (list) => {
    const i = list.indexOf("--root");
    if (i !== -1) {
      repoRoot = path.resolve(list[i + 1] || ".");
      list.splice(i, 2);
    }
    return list;
  };
  const args = takeRoot([...rest]);
  if (mode === "skills") skillRoots.push(...args);
  else if (mode === "claude") claudeFiles.push(...args);
  else if (mode === "both") {
    let bucket = null;
    for (const a of args) {
      if (a === "--skills") bucket = skillRoots;
      else if (a === "--claude") bucket = claudeFiles;
      else if (bucket) bucket.push(a);
    }
  } else {
    console.error(
      "usage: audit.mjs skills <dir>... | claude <file>... | both --skills <dir>... --claude <file>...\n" +
        "       --root <repo> enables the stale-reference and guard-duplication checks"
    );
    process.exit(2);
  }

  for (const root of skillRoots) {
    if (!fs.existsSync(root)) {
      console.error(`skipped, no such directory: ${root}`);
      continue;
    }
    for (const e of fs.readdirSync(root, { withFileTypes: true })) {
      const full = path.join(root, e.name);
      if (e.isDirectory() || e.isSymbolicLink()) {
        if (fs.existsSync(path.join(full, "SKILL.md"))) auditSkill(full);
      }
    }
  }
  crossSkillDuplicates();
  for (const f of claudeFiles) {
    if (fs.existsSync(f)) auditClaudeMd(f, repoRoot || path.dirname(path.resolve(f)));
    else console.error(`skipped, no such file: ${f}`);
  }

  const order = { error: 0, warn: 1, info: 2, stat: 3 };
  findings.sort((a, b) => order[a.level] - order[b.level] || a.target.localeCompare(b.target));
  const counts = { error: 0, warn: 0, info: 0, stat: 0 };
  for (const f of findings) counts[f.level]++;

  let current = null;
  for (const f of findings) {
    if (f.level !== current) {
      current = f.level;
      console.log(`\n== ${current.toUpperCase()} ==`);
    }
    console.log(`${f.target.padEnd(28)} ${f.rule.padEnd(24)} ${f.detail}`);
  }
  console.log(`\n${counts.error} errors, ${counts.warn} warnings, ${counts.info} notes`);
  process.exit(counts.error > 0 ? 1 : 0);
}

main();
