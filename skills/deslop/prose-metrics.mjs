#!/usr/bin/env node
// Structure, not vocabulary: the half of a prose sweep that no regular expression reaches.
//
// Three counts, each of which fails quietly on its own. A sentence past 40 words reads fluently and ends
// four lines later, so nobody notices it by eye. A semicolon in prose looks like punctuation rather than
// like a full stop somebody talked themselves out of. And a sentence split in the wrong place leaves two
// fragments that scan as sentences, so every later pass reads past them.
//
// Long doc summaries are listed after the counts and are not one. Length on its own does not say a
// summary has stopped summarizing: what does is a summary whose length is not earned by something a
// maintainer cannot re-derive, and that has to be read for. The list is where to start that read.
//
// The corpus comes from `scan.mjs --list` rather than from a second walk of the repository. That is the
// point: one config decides what is swept, one reader decides what counts as prose, and this script can
// never disagree with the vocabulary pass about which files exist. Whatever a repo put in `roots`,
// `docRoots`, `exempt` and `languages` already applies here with nothing to keep in step.
//
// Rules and thresholds: whichever annex the repo names in its deslop config.
//
//   node prose-metrics.mjs               the counts, worst first
//   node prose-metrics.mjs --all         every long sentence, not the worst 20
//   node prose-metrics.mjs --words 25    a different sentence threshold
//   node prose-metrics.mjs --since REF   only the prose written since REF, for a second pass

import { spawnSync } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const HERE = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const ALL = argv.includes('--all');
// 40 rather than STE's 25, because 25 flags 40% of this codebase and a list that long is not a list of
// defects, it is a second copy of the file. Past 40 a sentence has stopped being readable in one pass.
const LIMIT = (() => {
  const i = argv.indexOf('--words');
  const n = i >= 0 ? Number(argv[i + 1]) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 40;
})();

// Where the summary list starts, not where a summary becomes wrong. Ten lines is about the length past
// which one is worth reading to see whether it earns its size.
const SUMMARY_LINES = 10;

// --since is handed straight to the corpus builder, so both halves of a sweep narrow the same way and
// by the same definition of what counts as newly written.
const SINCE = (() => {
  const i = argv.indexOf('--since');
  return i >= 0 && argv[i + 1] ? argv[i + 1] : null;
})();

const args = [join(HERE, 'scan.mjs'), '--list', ...(SINCE ? ['--since', SINCE] : [])];
const out = spawnSync(process.execPath, args, { maxBuffer: 1 << 28 });
if (out.status !== 0) {
  process.stderr.write(out.stderr.toString());
  console.error('prose-metrics needs scan.mjs to produce the corpus. Fix that first.');
  process.exit(2);
}

// [0001] path/to/file.cs:12  // the comment text
//
// Under --since the star marks a line that changed. The rest of its block comes too, because a sentence
// has to be read whole to be measured, and is then held back out of the counts: a sentence is reported
// only where it holds a starred line, so a block is neither cut in half nor reported entire.
const corpus = out.stdout.toString('utf8').split('\n').flatMap(l => {
  const m = l.match(/^\[\d+([* ])\]\s+(.+?):(\d+)\s\s(.*)$/);
  return m ? [{ file: m[2], line: Number(m[3]), text: m[4], touched: m[1] === '*' }] : [];
});
if (!corpus.length) {
  console.log(SINCE
    ? `Nothing to count: no prose has been written since ${SINCE}. Changes since then were all in files `
      + 'this repo does not sweep, or outside its roots.'
    : 'No prose in the corpus. Check `roots` and `docRoots` in .claude/deslop.config.mjs.');
  process.exit(0);
}

// Strip what is code rather than writing: the comment markers themselves, XML doc tags, and markdown's
// own punctuation. A tag left in place would be counted as a word and would split a sentence at its dot.
//
// A block-level tag is a container wall, and a sentence never runs across one. Marking the walls is what
// keeps a <remarks> opening with an inline <paramref> from being read as the tail of the <summary> above
// it: the inline tag strips to nothing, the remainder starts lowercase, so the sentence split finds no
// boundary and reports the two as one sentence of their combined length.
const WALL = '\u0001';
const BLOCK_TAG =
  /<\/?(?:summary|remarks|para|returns|value|example|exception|list|item|term|description|typeparam)\b[^>]*>/gi;

// An inline tag renders as a word, so it is replaced by the word rather than deleted. Deleting it drops
// a word from the count and, where the tag opens a sentence, takes the sentence boundary with it.
const inlineNames = t => t
  .replace(/<(?:paramref|typeparamref)\b[^>]*\bname\s*=\s*"([^"]+)"[^>]*>/gi, '$1')
  .replace(/<(?:see|seealso)\b[^>]*\bcref\s*=\s*"(?:[A-Za-z]:)?([^"]+)"[^>]*>/gi, '$1')
  .replace(/<(?:see|seealso)\b[^>]*\b(?:langword|href)\s*=\s*"([^"]+)"[^>]*>/gi, '$1')
  .replace(/<c>([^<]*)<\/c>/gi, '$1');

const COMMENT_MARKER = /^\s*(\/\/\/?|\/\*+|\*+\/?|#+|--)\s?/;

const clean = t => inlineNames(t)
  .replace(COMMENT_MARKER, '')
  .replace(BLOCK_TAG, ` ${WALL} `)
  .replace(/<\/?[a-z][^>]*>/gi, ' ')
  .replace(/^[-*+]\s+|^\d+\.\s+|^>\s?/, '')
  .replace(/\s+/g, ' ')
  .trim();

// The same text with the walls taken back out, for whatever is judged or shown one line at a time.
const plain = t => clean(t).split(WALL).join(' ').replace(/\s+/g, ' ').trim();

// The comment marker gone, the list marker still on. This is what the list test has to read, because
// clean() strips both and a test run on its output can never see a bullet: every markdown list then
// joins into one running sentence, and a bullet opening in bold gives the splitter no capital to break
// on. A blockquote marker comes off first, so a bullet inside a note block is still a bullet.
//
// The indent is kept, because only the first line of a wrapped list item carries a marker. Dropping
// that line alone leaves the rest of the item standing in the prose, where its opening bracket has gone
// and its words run on into the paragraph below it.
const undecorated = t => inlineNames(t).replace(COMMENT_MARKER, '').replace(/^(?:>\s?)+/, '');
const indentOf = t => t.match(/^ */)[0].length;

// A run of comment lines on consecutive lines of one file is one block of prose. A gap of more than one
// line is a different thought, and joining across it would invent sentences nobody wrote.
const blocks = [];
for (const c of corpus) {
  const last = blocks[blocks.length - 1];
  if (last && last.file === c.file && c.line <= last.end + 1) {
    last.end = c.line;
    last.lines.push(c.text);
    last.touched.push(c.touched);
  } else {
    blocks.push({ file: c.file, start: c.line, end: c.line, lines: [c.text], touched: [c.touched] });
  }
}

// A new sentence may open with an identifier, which is lowercase about as often as not, so the split
// cannot require a capital. The guard is the other way round: an abbreviation's full stop does not end a
// sentence, and neither does an initial.
const SENTENCE =
  /(?<!\b(?:e\.g|i\.e|etc|vs|cf|approx|resp|[Nn]o|[Ff]ig|[Ee]q|[Rr]ef)\.)(?<!\b[A-Z]\.)(?<=[.!?])\s+(?=[A-Za-z(])/;

// A table row and a list item are not sentences: they are a vertical list, which is the shape this style
// asks for. Counting them as prose would report every well-formed table as the longest sentence in the
// repository.
const isList = l => /^\s*\|/.test(l) || /^\s*[-*+]\s/.test(l) || /^\s*\d+\.\s/.test(l);

// The one count that finds damage rather than style. Replacing a semicolon with a full stop is the
// commonest edit a prose pass makes, and it is safe only where the semicolon joined two whole sentences.
// Inside a parenthetical or a list it is not: the stop lands mid-phrase, the tail keeps its closing
// bracket or its last list item, and both halves then scan as sentences to anyone reading for length.
// Nothing else here catches that, because the fragments are short and their words are all spelled right.
//
// The other shape is a substitution that replaced one word of a pair and left the other, which reads as
// two articles in a row or as one word written twice. Both survive a re-read of the diff, since the eye
// supplies the missing edit, and both sit at a line break about as often as not, which is why this is
// measured on the joined sentence rather than a line at a time.
//
// Same case both times, or "an AND and either direction" is a defect four times over in one file: a
// technical vocabulary is full of capitalized words that are also ordinary ones, and the pair that a
// wrong edit leaves behind is written the same way twice. Not before a hyphen either, which is how
// "blacks out out-of-bounds pixels" reads as a repeat.
const DOUBLED = /\b([A-Za-z]+)\s+\1\b(?!-)/;
const TWO_ARTICLES = /\b(?:an?|the)\s+(?:an?|the)\b/i;

// A comment quoting a string is showing it, not writing it, and a naming routine's comment shows the
// wrong output on purpose. Only what falls outside the quotes is this file's own prose.
const quoted = (s, at) => (s.slice(0, at).match(/"/g) || []).length % 2 === 1;

// `source` is the block as written, with the inline tags still in it. A repeat has to survive there
// too, or `snaps to <paramref name="to"/>` reads as "to to" once the tag renders to its own name.
const brokenBy = (s, source) => {
  const opens = (s.match(/\(/g) || []).length;
  const closes = (s.match(/\)/g) || []).length;
  if (opens > closes) return 'opens a bracket it never closes';
  if (closes > opens) return 'closes a bracket it never opened';
  const twice = s.match(DOUBLED);
  if (twice && !quoted(s, twice.index) && DOUBLED.test(source)) return `writes "${twice[1]}" twice`;
  const articles = s.match(TWO_ARTICLES);
  if (articles && !quoted(s, articles.index)) return `two articles in a row, "${articles[0]}"`;
  return null;
};

const long = [], semis = [], summaries = [], broken = [];
for (const b of blocks) {
  // Each line keeps the number it came from and where its text lands in the joined prose, so a sentence
  // can be reported at the line it starts on rather than at the top of its block, and can be matched
  // against the lines that changed.
  // A list is a wall as well as a skip. Lifting the rows out and closing the gap joins the paragraph
  // above a table to the one below it, and the pair are then measured as one sentence that neither of
  // them is: a lead-in ending in a colon, a table, and whatever follows.
  const parts = [];
  let at = 0;
  let itemIndent = -1;
  let afterList = false;
  b.lines.forEach((l, k) => {
    const bare = undecorated(l);
    if (isList(bare.trim())) { itemIndent = indentOf(bare); afterList = true; return; }
    // Still inside the item above: same item, wrapped, so it is list and not prose.
    if (itemIndent >= 0 && bare.trim() && indentOf(bare) > itemIndent) { afterList = true; return; }
    itemIndent = -1;
    let t = clean(l);
    if (!t) return;
    if (afterList) { t = `${WALL} ${t}`; afterList = false; }
    parts.push({ t, from: at, to: at + t.length, line: b.start + k, touched: b.touched[k] });
    at += t.length + 1;
  });
  const prose = parts.map(p => p.t).join(' ');
  const source = b.lines.map(l => l.replace(COMMENT_MARKER, '')).join(' ');

  let cursor = 0;
  for (const segment of prose.split(WALL))
    for (const s of segment.split(SENTENCE)) {
      const from = prose.indexOf(s, cursor);
      if (from < 0) continue;
      const to = from + s.length;
      cursor = to;
      const holds = parts.filter(p => p.from < to && p.to > from);
      if (SINCE && !holds.some(p => p.touched)) continue;
      const line = holds.length ? holds[0].line : b.start;
      const words = s.trim().split(/\s+/).filter(Boolean).length;
      if (words > LIMIT) long.push({ file: b.file, line, words, text: s.trim() });
      const why = brokenBy(s.trim(), source);
      if (why) broken.push({ file: b.file, line, why, text: s.trim() });
    }

  b.lines.forEach((l, k) => {
    if (SINCE && !b.touched[k]) return;
    // Not a for-header, not a URL, and not an HTML entity: those are code inside prose.
    const t = plain(l);
    if (/;/.test(t) && !/https?:\/\//.test(t) && !/&\w+;/.test(t) && !/\bfor\s*\(/.test(t)) {
      semis.push({ file: b.file, line: b.start + k, text: t });
    }
  });

  // Collected to be read rather than counted, so the threshold picks where the reading starts.
  const open = b.lines.findIndex(l => /<summary>/.test(l));
  if (open >= 0) {
    const close = b.lines.findIndex((l, i) => i >= open && /<\/summary>/.test(l));
    const end = close >= 0 ? close : b.lines.length - 1;
    const len = end - open + 1;
    const grew = b.touched.slice(open, end + 1).some(Boolean);
    if (len > SUMMARY_LINES && (!SINCE || grew)) summaries.push({ file: b.file, line: b.start + open, len });
  }
}

const byFile = rows => {
  const m = new Map();
  for (const r of rows) m.set(r.file, (m.get(r.file) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
};

const section = (title, rows, render) => {
  console.log(`\n${title}: ${rows.length}`);
  if (!rows.length) return;
  for (const [f, n] of byFile(rows)) console.log(`  ${String(n).padStart(5)}  ${f}`);
  console.log('');
  for (const r of (ALL ? rows : rows.slice(0, 20))) console.log('  ' + render(r));
  if (!ALL && rows.length > 20) console.log(`  ... ${rows.length - 20} more, with --all`);
};

// The same shape as the counts above but without the tally, because a number beside these reads as a
// backlog and none of them is known to be wrong yet.
const candidates = (title, rows, render) => {
  if (!rows.length) return;
  console.log(`\n${title} -- candidates for the read, not defects:`);
  for (const r of (ALL ? rows : rows.slice(0, 10))) console.log('  ' + render(r));
  if (!ALL && rows.length > 10) console.log(`  ... ${rows.length - 10} more, with --all`);
};

long.sort((a, b) => b.words - a.words);
summaries.sort((a, b) => b.len - a.len);

console.log(`Corpus: ${corpus.length} prose lines in ${new Set(corpus.map(c => c.file)).size} files.`);
section(`Sentences past ${LIMIT} words`, long, r => `[${r.words}w] ${r.file}:${r.line}  ${r.text.slice(0, 110)}`);
section('Semicolons in prose', semis, r => `${r.file}:${r.line}  ${r.text.slice(0, 110)}`);
section('Sentences split in the wrong place', broken,
  r => `${r.file}:${r.line}  ${r.why}\n          ${r.text.slice(0, 110)}`);
candidates(`Longest doc summaries`, summaries, r => `[${r.len} lines] ${r.file}:${r.line}`);

console.log(`
NOT COUNTED here, and worth a read pass:
  - one list item or table cell that runs past the limit on its own
  - a condition trailing its command instead of leading it
  - two words for one concept across a document
  - three parallel sentences that wanted to be a table
  - a comment whose length is not earned by what it tells a maintainer
`);
