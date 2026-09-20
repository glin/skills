#!/usr/bin/env node
// The mechanical half of a deslop sweep: extract every comment in a repo's shipped source, match the
// rules that have fixed vocabulary, and count repeated phrasing across the whole corpus.
//
// It exists because reading a large file looking for bad writing is not repeatable. Attention runs out
// before the file does, so each pass finds a different subset and a file nobody is editing is never
// reached at all. This gives the sweep a denominator and the same answer every run.
//
// It does not replace the read. Several rules have no vocabulary to match, including the one that finds
// the most real defects: a comment that contradicts the code below it. Those are printed as unchecked on
// every run, because a quiet scan otherwise reads as "swept" when it means "the vocabulary is clean".
//
// The patterns live in SKILL.md, in the fenced block beside the rules they belong to, so editing a rule
// is editing what this looks for. Nothing here carries vocabulary of its own.
//
//   node scan.mjs                      scan the current repo
//   node scan.mjs --config <path>      a config other than .claude/deslop.config.mjs
//   node scan.mjs --list               print the numbered corpus, for the read pass
//   node scan.mjs --json               machine-readable output
import { readFileSync, existsSync } from 'fs';
import { spawnSync } from 'child_process';
import { join, relative, dirname, resolve, extname } from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};
const LIST = argv.includes('--list');
const JSON_OUT = argv.includes('--json');
const SINCE = arg('--since', null);

// ---------------------------------------------------------------- patterns

// A fenced ```json deslop-patterns block, wherever it appears in a markdown file. Kept in the document
// that explains the rules rather than in a sibling data file, so the two cannot be edited apart.
function patternsFrom(mdPath) {
  if (!existsSync(mdPath)) return { rules: {}, partial: [], unmechanizable: [] };
  const md = readFileSync(mdPath, 'utf8');
  const m = md.match(/```json deslop-patterns\r?\n([\s\S]*?)```/);
  if (!m) return { rules: {}, partial: [], unmechanizable: [] };
  let parsed;
  try {
    parsed = JSON.parse(m[1]);
  } catch (e) {
    console.error(`${mdPath}: the deslop-patterns block is not valid JSON -- ${e.message}`);
    process.exit(2);
  }
  return {
    rules: parsed.rules || {},
    partial: parsed.partial || [],
    codeOnly: parsed.codeOnly || [],
    unmechanizable: parsed.unmechanizable || [],
  };
}

// ---------------------------------------------------------------- config

// A config is optional. Without one this scans every tracked source file in the repo under the generic
// rules, which is the right default for a project that has no house vocabulary to declare. A config only
// ever narrows or adds: which trees ship, which words are the domain's own, which extra rules apply.
const configPath = resolve(arg('--config', join(process.cwd(), '.claude', 'deslop.config.mjs')));
const fileCfg = existsSync(configPath) ? (await import(pathToFileURL(configPath).href)).default : null;
const repoRoot = resolve(arg('--root', fileCfg ? dirname(dirname(configPath)) : process.cwd()));

// A project can declare its scope in the CLAUDE.md it already keeps, in a fenced json deslop-scope
// block. That suits a repository which does not vendor this skill and has no reason to carry a config
// file for it. The block takes the same keys a config does, minus `rules` and `annex`, which are code.
// A config file wins when both exist.
function scopeFrom(mdPath) {
  if (!existsSync(mdPath)) return {};
  const m = readFileSync(mdPath, 'utf8').match(/```json deslop-scope\r?\n([\s\S]*?)```/);
  if (!m) return {};
  try {
    return JSON.parse(m[1]);
  } catch (e) {
    console.error(`${mdPath}: the deslop-scope block is not valid JSON -- ${e.message}`);
    process.exit(2);
  }
}

const cfg = fileCfg || scopeFrom(join(repoRoot, 'CLAUDE.md'));

const skill = patternsFrom(join(HERE, 'SKILL.md'));
const annexes = (cfg.annex || []).map(p => patternsFrom(resolve(repoRoot, p)));

// Repo rules win over annex rules, which win over the skill's, so a repo can narrow one it must live with.
const rules = Object.assign({}, skill.rules, ...annexes.map(a => a.rules), toSource(cfg.rules || {}));
const partial = [...skill.partial, ...annexes.flatMap(a => a.partial)];

// Rules that are about a comment syntax rather than about writing, so a document must not be judged by
// them. `**bold**` renders as nothing in a C# doc comment and is correct in a README; `&gt;` is an
// over-escape in XML and ordinary text elsewhere. Without this a markdown file answers to the rules of
// whatever language the annex was written for, and the false positives bury the real hits.
const codeOnly = new Set([...skill.codeOnly, ...annexes.flatMap(a => a.codeOnly), ...(cfg.codeOnly || [])]);
const unchecked = [...skill.unmechanizable, ...annexes.flatMap(a => a.unmechanizable), ...(cfg.unmechanizable || [])];

// A config may write a rule as a literal regex; normalise to source strings so every rule is built the same way.
function toSource(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) out[k] = v instanceof RegExp ? v.source : v;
  return out;
}
const compiled = Object.entries(rules).map(([name, src]) => [name, new RegExp(src, src.match(/[A-Z]/) ? '' : 'i')]);
const falsePositives = (cfg.knownFalsePositives || []).map(s => new RegExp(s instanceof RegExp ? s.source : s, 'i'));
const allowCaps = new Set([...(cfg.allowCaps || []), 'A', 'I', 'AND', 'OR', 'NOT', 'IF', 'THE']);
// The repo's own vocabulary, without the words above. Those are here to stop a run made only of
// them reading as shouting. They are not a licence to shout one of them on its own.
const repoCaps = new Set(cfg.allowCaps || []);
// Ordinary English that means nothing extra in capitals. A run needs two adjacent words, so one
// shouted word is invisible to it, and one word is the commoner form.
//
// MUST, NEVER, ALWAYS and REQUIRED are deliberately absent. A capital earns its place on a warning
// whose cost is silent and whose reader has to stop. The rest is emphasis a stronger sentence
// carries better. Names keep their case, and a project adds its own to `allowCaps`.
const SHOUT = new Set(('ALL ALSO ANOTHER ANY AND BECAUSE BEFORE BOTH BUT CURRENT DIRECTLY EACH '
  + 'EITHER EMPTY ENTIRE EVEN EVERY EXACT EXACTLY FEW FIRST FOR FULL HERE JUST LAST LESS MANY MORE '
  + 'MUCH NEITHER NEW NEXT NO NOT NOW OLD ONCE ONE ONLY OR OWN PER REAL SAME SECOND SO STILL THAT '
  + 'THEN THERE THIRD THIS THREE TWO UNTIL VERBATIM VERY WHEN WHICH WHILE WHOLE WITH WITHOUT YES'
).split(' '));

// ---------------------------------------------------------------- corpus

// How to find a comment, by extension. This is language knowledge, which is the skill's to hold; which
// extensions a project actually ships is not, and comes from its config's `languages`.
const SYNTAX = {
  '.c': 'c', '.h': 'c', '.cpp': 'c', '.hpp': 'c', '.cs': 'c', '.java': 'c', '.go': 'c', '.rs': 'c',
  '.js': 'c', '.mjs': 'c', '.cjs': 'c', '.ts': 'c', '.tsx': 'c', '.jsx': 'c', '.swift': 'c', '.kt': 'c',
  '.py': 'python', '.rb': 'hash', '.sh': 'hash', '.bash': 'hash', '.yml': 'hash', '.yaml': 'hash',
  '.toml': 'hash', '.pl': 'hash', '.r': 'hash',
  '.md': 'prose', '.markdown': 'prose', '.txt': 'prose',
};
Object.assign(SYNTAX, cfg.languages || {});

// The file list comes from git, so a project's build output, vendored trees and virtualenvs are already
// excluded by the .gitignore it already maintains. There is no list of directory names to keep current
// here, and nothing to know about any one ecosystem's conventions.
function tracked() {
  const out = spawnSync('git', ['ls-files', '-z'], { cwd: repoRoot, maxBuffer: 1 << 28 });
  if (out.status !== 0) {
    console.error('Not a git repository, or git is unavailable. This reads the tracked file list.');
    process.exit(2);
  }
  return out.stdout.toString('utf8').split('\0').filter(Boolean);
}

// Walks the source tracking string state, so a // inside a literal is never read as a comment. That
// matters most where a project carries another language's source inside a string: shader code in a C#
// verbatim string is not prose to sweep, and its em-dashes and capitals are not defects.
function commentsC(src) {
  const out = [];
  let i = 0, line = 1;
  const n = src.length;
  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (c === '\n') { line++; i++; continue; }
    if (c === '@' && d === '"') {
      i += 2;
      while (i < n) {
        if (src[i] === '"' && src[i + 1] === '"') { i += 2; continue; }
        if (src[i] === '"') { i++; break; }
        if (src[i] === '\n') line++;
        i++;
      }
      continue;
    }
    if (c === '`') { i++; while (i < n && src[i] !== '`') { if (src[i] === '\\') i++; else if (src[i] === '\n') line++; i++; } i++; continue; }
    if (c === '"' || c === "'") {
      const q = c;
      i++;
      while (i < n && src[i] !== q) { if (src[i] === '\\') i++; else if (src[i] === '\n') { line++; break; } i++; }
      i++;
      continue;
    }
    if (c === '/' && d === '/') {
      let j = i; while (j < n && src[j] !== '\n') j++;
      out.push({ line, text: src.slice(i, j).trim() });
      i = j; continue;
    }
    if (c === '/' && d === '*') {
      let j = i + 2, first = line;
      while (j < n && !(src[j] === '*' && src[j + 1] === '/')) { if (src[j] === '\n') line++; j++; }
      src.slice(i, Math.min(j + 2, n)).split('\n').forEach((t, k) => { if (t.trim()) out.push({ line: first + k, text: t.trim() }); });
      i = j + 2; continue;
    }
    i++;
  }
  return out;
}

// Python keeps most of its prose in docstrings, so reading only # lines misses the documentation and
// reports a file as clean when nothing of it was read.
//
// A docstring is a string literal in expression position: the first statement of a module, class or
// function. Recognising that exactly needs a parser, so the test here is that the string opens its own
// line, which is true of every docstring and false of the two shapes that hold data rather than prose,
// `x = """..."""` and `f("""...""")`. A module-level triple-quoted block used as data would still be
// read; put it in the repo's knownFalsePositives if one shows up.
//
// Tracking the open delimiter also fixes a smaller error: a # inside a docstring is prose, not a comment,
// and a line-by-line scan counts it twice.
// Markdown and plain text, where the whole file is the prose and there is no comment to find. A README
// is the case the rules about user-facing writing were written for, and reading only source comments
// reports one as clean when nothing of it was read.
//
// Fenced blocks are the exception. A code sample is source that happens to sit in a document, so its
// capitals, its identifiers and its own comments belong to the code and not to the writing.
//
// These files answer to `docRoots` rather than `roots`, because the tree holding a project's prose is
// rarely the tree holding its code. A repo that declares no docRoots gets no prose files, so this
// reader changes nothing for a project until it asks.
function commentsProse(src) {
  const out = [];
  let fence = null;
  src.split('\n').forEach((raw, k) => {
    const s = raw.trim();
    const open = s.match(/^(```|~~~)/);
    if (fence) { if (open && s.startsWith(fence)) fence = null; return; }
    if (open) { fence = open[1]; return; }
    if (s) out.push({ line: k + 1, text: s });
  });
  return out;
}

// Shell, YAML and the rest: a # comment and nothing else to track.
function commentsHash(src) {
  return src.split('\n').flatMap((t, k) => {
    const s = t.trim();
    return s.startsWith('#') && !s.startsWith('#!') ? [{ line: k + 1, text: s }] : [];
  });
}

function commentsPython(src) {
  const out = [];
  const lines = src.split('\n');
  let open = null;              // the delimiter we are inside, or null
  for (let k = 0; k < lines.length; k++) {
    const raw = lines[k];
    const s = raw.trim();
    if (open) {
      out.push({ line: k + 1, text: s });
      if (s.includes(open)) open = null;
      continue;
    }
    if (s.startsWith('#')) {
      if (!s.startsWith('#!')) out.push({ line: k + 1, text: s });
      continue;
    }
    // A string prefix (r, b, u, f and their combinations) may precede the quotes.
    const m = s.match(/^[rRbBuUfF]{0,2}("""|''')/);
    if (!m) continue;
    const delim = m[1];
    const rest = s.slice(m[0].length);
    out.push({ line: k + 1, text: s });
    if (!rest.includes(delim)) open = delim;   // stays open past this line
  }
  return out;
}

// Repo-relative, so they compare directly against git's own paths.
const roots = (cfg.roots || ['']).map(r => r.replace(/\\/g, '/').replace(/\/+$/, ''));
const exemptRe = (cfg.exempt || []).map(g => new RegExp('^' + g.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*\*/g, '.*').replace(/(?<!\.)\*/g, '[^/]*') + '$'));

const READ = { c: commentsC, python: commentsPython, hash: commentsHash, prose: commentsProse };

// Prose lives in its own trees. Undeclared means a project has not asked for its documents to be read,
// and none are, which is what every repo written before this reader existed expects.
const docRoots = (cfg.docRoots || []).map(r => r.replace(/\\/g, '/').replace(/\/+$/, ''));
const inDocRoots = rel => docRoots.some(r => r === '' || rel === r || rel.startsWith(r + '/'));
// An empty root is the whole repository, which is what a project with nothing to declare gets.
const inRoots = rel => roots.some(r => r === '' || rel === r || rel.startsWith(r + '/'));

const corpus = [];
for (const rel of tracked().sort()) {
  const syntax = SYNTAX[extname(rel).toLowerCase()];
  if (!syntax || exemptRe.some(re => re.test(rel))) continue;
  if (!(syntax === 'prose' ? inDocRoots(rel) : inRoots(rel))) continue;
  let src;
  try { src = readFileSync(join(repoRoot, rel), 'utf8'); } catch { continue; }
  for (const c of READ[syntax](src)) corpus.push({ file: rel, line: c.line, text: c.text, syntax });
}

// ---------------------------------------------------------------- --since

// A second pass over a branch that has moved on wants what the newer commits wrote, not the whole
// corpus again: the rest was read once already, and re-reading it buries the new prose in old hits.
// The narrowing is by added line, working tree against the ref, so uncommitted work counts too.
// The prefixes are pinned because `diff.mnemonicPrefix` rewrites them per comparison (c/, w/, i/), and a
// parser expecting b/ then matches no file and narrows the corpus to nothing while reporting a clean run.
function addedSince(ref) {
  const out = spawnSync('git',
    ['diff', '-U0', '--src-prefix=a/', '--dst-prefix=b/', ref, '--'],
    { cwd: repoRoot, maxBuffer: 1 << 28 });
  if (out.status !== 0) {
    console.error(`--since ${ref}: git could not diff against that ref.`);
    process.exit(2);
  }
  const ranges = new Map();
  let file = null;
  for (const line of out.stdout.toString('utf8').split('\n')) {
    const f = line.match(/^\+\+\+ b\/(.*)$/);
    if (f) { file = f[1]; continue; }
    const h = line.match(/^@@ .* \+(\d+)(?:,(\d+))? @@/);
    if (!h || !file) continue;
    const start = Number(h[1]);
    const count = h[2] === undefined ? 1 : Number(h[2]);
    if (count <= 0) continue;
    if (!ranges.has(file)) ranges.set(file, []);
    ranges.get(file).push([start, start + count - 1]);
  }
  return ranges;
}

// A sentence is measured wherever it ends, not where it was edited, so keeping the changed lines alone
// cuts sentences in half and then reports both halves as short. Every hit grows back out to the run of
// consecutive prose lines around it: a doc block in code, a paragraph in a document, because a blank line
// is not prose and so ends the run. Without this, editing one line of a long sentence narrows it away and
// the structure counts come back clean over prose no reader ever saw whole.
function wholeBlocks(touched, all) {
  const byFile = new Map();
  for (const c of all) {
    if (!byFile.has(c.file)) byFile.set(c.file, []);
    byFile.get(c.file).push(c);
  }
  for (const list of byFile.values()) list.sort((x, y) => x.line - y.line);
  const keep = new Set(touched);
  for (const hit of touched) {
    const list = byFile.get(hit.file) || [];
    const at = list.indexOf(hit);
    if (at < 0) continue;
    for (let i = at; i > 0 && list[i - 1].line === list[i].line - 1; i--) keep.add(list[i - 1]);
    for (let i = at; i < list.length - 1 && list[i + 1].line === list[i].line + 1; i++) keep.add(list[i + 1]);
  }
  return all.filter(c => keep.has(c));
}

if (SINCE) {
  const ranges = addedSince(SINCE);
  if (!ranges.size) {
    console.error(`--since ${SINCE}: that ref and the working tree differ in no file, so there is nothing `
      + `newer to read. Narrowing here would report a clean run over an empty corpus.`);
    process.exit(2);
  }
  const all = corpus.slice();
  const touched = all.filter(c => (ranges.get(c.file) || []).some(([a, b]) => c.line >= a && c.line <= b));
  if (!touched.length) {
    console.error(`--since ${SINCE}: files changed since that ref, but none of the changes are in prose this `
      + `sweep reads. Narrowing here would report a clean run over an empty corpus.`);
    process.exit(2);
  }
  for (const c of touched) c.touched = true;
  corpus.length = 0;
  corpus.push(...wholeBlocks(touched, all));
  // To stderr under --json, or the notice lands in the document a caller is about to parse. It is still
  // printed rather than dropped, because a narrowed run that does not say so is read as a whole-repo one.
  if (!LIST) (JSON_OUT ? process.stderr.write : console.log).call(
    JSON_OUT ? process.stderr : console,
    `Narrowed by --since ${SINCE}: ${touched.length} of ${all.length} prose lines changed, `
    + `read in the ${corpus.length} lines of the blocks holding them. Everything older is unread in this run.\n`);
}

if (LIST) {
  // The star marks a line that changed, against the rest of its block, which is here so a sentence can be
  // read whole. Only a starred line is a hit, in this script and in the one that measures sentences.
  corpus.forEach((c, k) => console.log(
    `[${String(k + 1).padStart(4, '0')}${c.touched ? '*' : ' '}] ${c.file}:${c.line}  ${c.text}`));
  process.exit(0);
}

// ---------------------------------------------------------------- rules

const hits = [];
for (const c of corpus) {
  // Under --since the corpus carries whole blocks so a sentence can be read whole, but a word is a hit
  // only where it was written. Reporting the rest would hand back prose an earlier sweep already cleared.
  if (SINCE && !c.touched) continue;
  for (const [name, re] of compiled) {
    if (c.syntax === 'prose' && codeOnly.has(name)) continue;
    const m = c.text.match(re);
    if (!m) continue;
    // A known false positive may name the word alone ("the old \w+") or the phrase that makes it
    // legitimate ("state machine behaviour"), so it is tried against both.
    if (falsePositives.some(fp => fp.test(m[0]) || fp.test(c.text))) continue;
    hits.push({ rule: name, file: c.file, line: c.line, match: m[0], text: c.text });
  }
  // Words capitalized only to shout. Acronyms and identifiers keep their case, so a run is only a hit
  // when it contains something the repo has not claimed as its own vocabulary.
  const masked = c.text.replace(/`[^`]*`/g, ' ');
  const runs = masked.match(/\b[A-Z]{2,}\b(?:[ \t]+\b[A-Z]{2,}\b)+/g) || [];
  for (const run of runs) {
    if (!run.split(/\s+/).some(w => !allowCaps.has(w))) continue;
    if (falsePositives.some(fp => fp.test(run))) continue;
    hits.push({ rule: 'all-caps', file: c.file, line: c.line, match: run, text: c.text });
  }
  // Then one word on its own, which a run can never contain. The runs are cut out first, so each
  // word is reported once by whichever pass owns it.
  let alone = masked;
  for (const run of runs) alone = alone.replace(run, ' ');
  for (const word of alone.match(/\b[A-Z]{2,}\b/g) || []) {
    if (!SHOUT.has(word) || repoCaps.has(word)) continue;
    if (falsePositives.some(fp => fp.test(word) || fp.test(c.text))) continue;
    hits.push({ rule: 'all-caps', file: c.file, line: c.line, match: word, text: c.text });
  }
}

// Repeated phrasing. A phrase reads fine once and as a tic at five occurrences, which no single-comment
// review can see. Domain vocabulary repeats legitimately, so these are candidates and never failures.
const grams = new Map();
for (const c of corpus) {
  if (SINCE && !c.touched) continue;
  const w = c.text.replace(/^[/#*\s]+/, '').toLowerCase().replace(/[^a-z0-9' ]/g, ' ').split(/\s+/).filter(Boolean);
  for (let n = 5; n <= 7; n++)
    for (let i = 0; i + n <= w.length; i++) {
      const key = w.slice(i, i + n).join(' ');
      if (!grams.has(key)) grams.set(key, new Set());
      grams.get(key).add(`${c.file}:${c.line}`);
    }
}
const repeats = [...grams.entries()].map(([p, s]) => [p, [...s]]).filter(([, w]) => w.length >= (cfg.repeatThreshold || 6));
repeats.sort((a, b) => b[1].length - a[1].length || b[0].length - a[0].length);
const topRepeats = [];
for (const [p, w] of repeats) {
  if (topRepeats.some(([q, v]) => q.includes(p) && v.length === w.length)) continue;
  topRepeats.push([p, w]);
}

// ---------------------------------------------------------------- report

if (JSON_OUT) {
  // Each hit says whether its rule is partial, which is the difference between a hit somebody has to
  // read and one that is a defect on sight. A build gate fails on the second kind only, and the partial
  // list stays here rather than being restated by every caller that needs to tell them apart.
  const isPartial = new Set(partial);
  console.log(JSON.stringify({
    scanned: corpus.length,
    hits: hits.map(h => ({ ...h, partial: isPartial.has(h.rule) })),
    unchecked,
    repeats: topRepeats.slice(0, 20),
  }, null, 2));
  process.exitCode = hits.length ? 1 : 0;
} else {
  const files = new Set(corpus.map(c => c.file)).size;
  console.log(`comments ${corpus.length} in ${files} files   rules ${compiled.length}   hits ${hits.length}\n`);
  const byRule = {};
  for (const h of hits) byRule[h.rule] = (byRule[h.rule] || 0) + 1;
  for (const [r, n] of Object.entries(byRule).sort((a, b) => b[1] - a[1])) console.log(`${String(n).padStart(4)}  ${r}`);
  if (hits.length) {
    console.log('');
    for (const h of hits) console.log(`  ${h.rule.padEnd(15)} ${h.file}:${h.line}  <<${h.match}>>  ${h.text.slice(0, 110)}`);
  }
  if (topRepeats.length) {
    console.log('\nrepeated phrasing -- candidates for the read, not defects:');
    for (const [p, w] of topRepeats.slice(0, 12)) console.log(`${String(w.length).padStart(4)}x  "${p}"   ${w[0]} ...`);
  }
  console.log('\nNOT CHECKED by this scan, and where the real defects have been:');
  for (const u of unchecked) console.log(`  - ${u}`);
  if (partial.length) console.log(`  (partially checked, vocabulary only: ${partial.join(', ')})`);
  console.log('\nRead the corpus against the code for those:  node scan.mjs --list');
  process.exitCode = hits.length ? 1 : 0;
}
