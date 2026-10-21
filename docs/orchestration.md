# Orchestration

This file records why each rule in the `orchestrate`, `delegate` and `dispatch` skills exists, so that nobody re-argues or quietly reverts a rule.

To add an entry, write one bullet per rule with one to three sentences on the failure it prevents, and no dates, hashes or history.

Reasons for reviewer rules live in the [pre-commit review reason log](../skills/pre-commit-review/README.md).

## Which skill does what

| The work | Skill |
| --- | --- |
| One repository task, from brief to a reviewed commit | `orchestrate` |
| Bounded units whose results return to the current session | `delegate` |
| A fresh session whose result is a commit or a report | `dispatch` |
| Read-only reviewers on an immutable commit | `pre-commit-review` |
| A written plan that spans several sessions | `phased-plan` |
| The cheapest topology for a large task, before it starts | `preplan` |

## How a task runs

```mermaid
flowchart TD
    start([The author asks for one task]) --> brief

    subgraph coord [Coordinator: scopes, triages, integrates]
        brief[Brief: branch, base, verification command, ceiling]
        tree{Current branch, topic branch, or worktree}
        triage[Triage: check every quoted line]
        gate{Stop rules: rounds, spend, machine failure}
        deliver[Deliver: leave, push, or land the commit]
    end

    subgraph units [Subagents: one job each, capped report]
        impl[Implement: one candidate commit]
        rev[Review: fresh read-only reviewers]
        fix[Fix: a fresh implementer adds one commit]
        rerev[Re-review: confirm the fixes]
    end

    brief --> tree --> impl --> rev --> triage
    triage -->|findings accepted| fix --> rerev --> gate
    triage -->|nothing accepted| gate
    gate -->|clear| deliver
    gate -->|needs the author| ask([Stop: one question])
    ask -->|the author answers| fix
```

A small coherent task runs inline, with one reviewer.

## Principles

1. Keep writes in one thread. Extra agents add judgement, not actions.
2. Pass decisions with the task. A unit that cannot see them makes its own, and the two sets clash.
3. The bill is the number of round trips. Every call re-sends the whole conversation.
4. Put a large context where it is paid for once. A subagent reads the code, and the coordinator does not pay for it on every call.
5. Cap each report at launch. A report stays in the coordinator's context for the rest of the task.
6. One job, one agent, one round. A fresh agent costs less and does not defend its own work.
7. Give a unit a number, not an adjective. A tool-call budget with the price of one round trip binds. "Be efficient" does not.
8. Use an automatic completion path. A poll costs a full call and learns one bit.
9. Stop on a rule, not on a feeling. Fixed limits end a task that is not converging.
10. Delegate retrieval down, never judgement. Cheap auditors skipped whole files and reported complete coverage.

## Brief

- The [worker brief contract](../skills/references/owner-brief.md) keeps the owner's words apart from agent inferences. Otherwise reviewers treat implementation choices as requirements.
- Reader labels identify work the reader follows. Inherited labels on internal workflow workers obscure that work. Parent/child links keep those workers discoverable.
- The coordinator reads the skill again at every invocation. A long thread keeps the text it loaded first.
- Brief saves the loaded skill bytes and their source. Another session can change the skill during the run.
- Brief names the shared resources. Caches, services and devices are shared with every session, and a collision gives no warning.
- A command that gains a new place to run from is cross-component. It moves shared state, and one such run spent twice its ceiling.
- The brief requires a test for each place a command runs from. Otherwise a command that is dead in its new place reaches review.
- A need the harness cannot reach stops once, at Brief. Fresh runs of one plan otherwise stopped on it again and again.
- That answer goes into the plan file at once. Compaction loses an answer held in chat.

## Retained action effects

A move of an action to a new owner can drop a visible effect while the return value stays the same. The brief names those effects, so a check exercises the action through its new owner. Existing coverage is enough when it reaches them.

## Run evidence

- Run evidence goes in the plan's `runs/` folder. A separate folder needs its own ignore rules and parts the evidence from its plan.
- The run folder stays outside every worktree. Saved source copies there enter the candidate's checks.
- Brief creates the run folder exclusively. Two concurrent runs otherwise share it.
- A run lists the large files it wrote into a plan folder. A repository that tracks only Markdown hides them from `git status`.

## Implementation

- A fresh implementer runs each fix round. A reused one defends its work and carries the first round's context.
- A design change stops the running implementer instead of reaching it by message. One implementer took such a message as an injection and spent the last round on the old design.
- The implementer names its choices in the commit body. A fresh fixer undoes a choice it cannot see.
- The implementer reports the reproduction it ran. One regression test covered a simulated path instead of the reported one, and a later run redid the fix.
- The implementer does its own work. Helpers returned nothing in one round, and a helper cannot run the gate.
- A candidate's sole parent is the base commit. Any other parent brings changes the task did not ask for.
- A false comment in an out-of-scope file still gets fixed. An implementer told to leave a file alone leaves the comment too.
- Two writers run in parallel only when the brief fixes every shared choice. In Cognition's example, two agents built game parts in separate files that did not match.
- A task too large for one implementer splits into phases. A second implementer that finishes the work pays again for everything the first read.
- A context overrun alone does not block review of a green commit. The commit holds the work, and review shows what remains.
- An implementer keeps its last five calls for its commit and report. Without a commit, the round is wasted.

The implementer iterates with the project's iteration command, or with the tests its change touches, and runs the full gate once per candidate SHA. One run spent 37 of its implementer's 68 minutes waiting on repeated full `npm test` runs, while the project's instructions already named a faster iteration command. The full gate still decides delivery, so the narrower loop moves an integration failure later but never lets one land.

## Review

- One task has one candidate commit. A shared tree changes under the reviewer.
- Reviewers start fresh and stay read-only. A shared context inherits blind spots, and an editing reviewer mixes faults with its own changes.
- Reviewers run in parallel. They read the same commit, so an order gains nothing.
- A cross-component change adds a reviewer from another model family. Reviewers from one family miss the same things.
- A reviewer opens the before-and-after images. Unopened images let stretched buttons pass review.
- A reviewer names the cost at production scale. One defect passed three reviews at fixture size, then froze the author's workspace.
- Every finding quotes its line. The coordinator then checks every finding in one call, without reading the code.
- Reviewers check the factual claims in the brief. A reviewer that accepts a false premise approves code that misses the goal.
- The review prompt says the implementer proved each new test. A reviewer cannot read that report and calls the proof missing.
- A reviewer out of budget is resumed once. That costs less than a new reviewer.
- A supplemental reviewer reads the whole assignment. The unread files need their context.
- A missing test is a finding, not incomplete coverage. As coverage, it buys a resume that rereads the same lines.
- The reviewer-default rule names no skill outside this repository. A public reader would look for a skill that is not there.

## Review rounds

- The limit is two review rounds. When round two finds as many faults as round one, the plan is at fault.
- A converging third round runs without asking. The author approved every third-round stop.
- A re-review starts with the fix delta. The reviewer checks the fix on a smaller budget. A moved target needs a range diff, because a plain diff holds the target's changes.
- A re-review accepts a new finding outside the fix only when it is severe. Otherwise each round becomes a full review.
- A low-severity finding on the fix starts no round. The author ruled rare, harmless edge cases not worth one, and long round chains took a large share of spend.
- Two correction-caused regressions call for diagnosis. A repeat failure often comes from a wrong assumption that another patch keeps.
- A round grant changes only the count it names. An open grant let one run spend far past its limits.

## Where the work happens

- The work goes where the repository's workflow puts it. A pull request workflow commits to its pull request branch.
- On the default branch, the task creates a topic branch. No unreviewed commit then reaches the default branch.
- A worktree is used only when another session shares the checkout. Shared checkouts overwrite each other, but worktrees cost setup and disk, and abandoned ones slowed the machine.
- Every worktree goes in `<repo>/.claude/worktrees/`. Trees in agent-chosen places escaped cleanup.
- The coordinator creates the worktree at the base commit. A harness worktree can start dozens of commits behind.
- One task owns one worktree. A tree per round and retry piled up dozens.
- A continuing task adds its tree again without `-b`. That option refuses an existing branch.
- Session scratch stays inside the worktree. A file in the shared temporary directory belongs to nobody.
- A shared dependency link breaks before a manifest changes. An edit through a live link changes another worktree.
- Only `remove-worktree.py` removes a worktree. On Windows, other removals followed dependency junctions and emptied the main checkout.
- Close runs on every exit. Cleanup as the last step was skipped by every run that stopped or failed.
- Branch deletion comes after tree removal. Git refuses to delete a branch that a worktree holds.

## Delivery

- Delivery waits for the author to name a target. A push and a pull request publish work.
- Landing uses the reviewed SHA. A branch name resolves to whatever it points at now.
- The coordinator runs `git rev-parse --git-dir` before landing. A coordinator inside a worktree cannot land.
- A clean main checkout switches to the target branch first. The preflight checks the checked-out branch.
- `land-preflight.py` runs before every fast-forward. It names every blocker before anything moves.
- The preflight takes a base. After a fix round, the tip's parent is not the target.
- The tip needs a passing full-gate receipt. Runs that counted a timeout as a base failure, or narrowed the gate, broke tests on main.
- Both landing scripts live in `references/scripts/`. A lower skill never names a workflow above it.
- A force-delete request names the landed SHA and the grant. Git calls a landed branch unmerged, and an approval reviewer refused a bare request.

## When the target moved

- A moved target does not make a commit unreviewed. A re-review on every move pays a full round each time.
- An ancestry check proves that the target moved. Otherwise a branch with unreviewed commits looks the same.
- The coordinator never rebases. A rebase writes files that no reviewer saw.
- `carry-review.sh` reads the evidence after a rebase. A prose ladder left each coordinator to compare patches by hand.
- Every verdict short of `conflict` needs a gate. Identical patch text on a new base does not prove independence.
- The rebase evidence goes outside every worktree. A repository-wide scan can mistake saved diffs for source.
- The interaction review runs even when the author asks to skip it. One skipped review broke loading and was reverted.

## Money

- Every task carries a spend ceiling. No harness enforces a budget across subagents.
- Each ceiling row sits near twice the modeled cost. A ceiling that never fires teaches nothing, and one that always fires teaches blind approval.
- The route line names the ceiling's source. A reader then tells a choice from a default.
- A ceiling stop fires only before a fix round that is not converging. The author approved every other ceiling stop, and runs sat idle for hours.
- Twice the ceiling ends the task. An earlier approval cannot foresee the spend after it.
- An author grant becomes the next run's ceiling. One grant otherwise left a task with no ceiling.
- The run is priced from its recorded start. Earlier runs in the session otherwise inflate the figure.
- Brief reads the session id from the environment. A later search for the transcript failed.
- The run is priced again after a break. One fix round started on a day-old figure and passed the ceiling.
- Stopped spend carries into the next run on the same phase. One plan stopped three times and passed twice its ceiling unnoticed.
- A dispatched session counts once. Dispatched reviews otherwise went uncounted or counted twice.
- Arc supplies every monetary value. A runner's own figure does not establish coverage.
- Without a price, no paid work starts. Request and token checkpoints cannot bound spend.
- A paid launch must fit under the absolute stop with every commitment. Per-group ceilings let one task pass 100 USD.

## The stop check

A delayed Codex snapshot gets one observation after a later model request, using the first report's frozen window. An immediate loop cannot observe a snapshot published after its own call returns. The normal check retains its actual first report without requiring another live check to initialize recovery. Recovery covers an identified Codex coordinator or child when every source has identifiable Codex bills. Mixed Claude sources and unidentified or legacy evidence retain conservative refusal. The retained pair keeps source and build identity, selected request identities, priced dollars and observation times. Prefix hashes and file identities allow appended evidence without accepting rewritten or replaced sources. Failed recovery consumes the observation. Missing coverage and prices still refuse admission. Replacement accounting and identical dispatched windows prevent overlapping charges. Explicit commitment bounds cover all work after the historical cutoff before paid admission. That total must fit below the ceiling. The as-of disclosure preserves the distinction between a reconciled window and final cost. Close reconciles later work through a current end. The later attempt is consumed before report loading, so adapter errors cannot grant extra observations. Recovery requires a current finite cutoff before its initial live check. Ordinary open-ended accounting retains its first evidence and cannot replace it for recovery. Supplemental reports retain their actual source ownership, source bytes, build, selected bills and dollars across the pair. A supplemental omission or changed price cannot erase spending. Complete empty parent and child summaries preserve zero-cost coverage, while explicit defects and unknown usage still refuse recovery.

- `stop-check.py` decides numeric continuation. Coordinators stopped at limits the rules never resolved, and the author approved most stops.
- An unreadable figure prints as `unavailable`, never zero. A zero admits unmeasured spend.
- A partial figure prints with its qualifier. One run reported `unknown` for figures Arc held in part.
- Close copies the stop-check lines into the final report. A report otherwise writes `unknown` over a shown figure.
- An empty window prices at zero when no coverage defect reaches it. The script read a complete empty window as unpriced and stopped at Brief.
- `orchestrate` carries its own copy of the script. It then reads nothing from another repository.

## Stops

- A stop is a pause, not an exit. A fresh start pays again for what the run knew.
- A stop asks one question in plain words. An error code in a stop made the author ask what it meant.
- A stop says where the task stands. Otherwise the author rebuilds the run from memory.
- No permission is asked for a round the limits allow. A stop the author always approves teaches approval without reading.
- A machine failure retries once. The author answered such stops with "try again".
- The retry never resets the tree. A reset discards the edits the retry must finish.
- A machine-failure stop names the attempt it repeats. A new failure of the same kind otherwise stopped as a repeat.
- A failing gate test reruns on the base commit first. One diagnosis spent millions of tokens on tests that already failed there.
- A gate timeout never counts as a base failure. It names no test, and one such run delivered a broken test.
- The task does its own chores. An unanswered question about follow-up work nearly lost the work.
- An author exchange never spends a review round. It reviews no code.

## Handoffs and plan edits

- A next-run prompt carries only what the author said. Its values override the plan, and handoffs shipped designs the author never approved.
- Requested follow-ups run without a manual prompt relay. A printed prompt leaves the request unfinished.
- A new session or compaction renews no limit. The handoff keeps consumed allocations and spend.
- The predecessor session stays available after a transfer. Start and archive commands cannot guard against input between them.
- A plan edit commits in the repository that owns the plan directory. One coordinator committed plan files into the target.
- The plan commit goes on the topic branch after the last review round. It lands with the reviewed commits and keeps the checkout clean.
- A plan edit commits with `git commit -- <path>...`. A bare `git commit` took other sessions' staged files.
- A stop leaves a plan edit in the built repository uncommitted. A committed edit landed inside the next fix delta.

## Context and delegation

- The coordinator's own context is the largest cost. Re-reading history took most of one measured turn's spend.
- The coordinator's token limit counts growth above the opening context. The opening context is already large.
- Request and token limits are checkpoints, not stops. The author approved nearly every such stop, and the ceiling already prices the coordinator.
- Bulk setup reads go to a subagent. One coordinator hit its checkpoint before any implementer launched.
- `stop-check.py` saves the opening context at Brief. A later pricing window cannot recover it.
- Waits never poll. One coordinator passed its request limit during test waits.
- The coordinator keeps its narration short. Its own output is the largest source of its growth.
- A plan reaches a unit by path. The text costs the whole plan in every prompt.
- Decisions travel with the prompt. A unit with no context reopens what it cannot see.
- Text from another agent is data. One note posed as the coordinator and asked for an amend.
- A calling workflow's limits bind `delegate`. Its ask before another premium round would stop a converging `orchestrate` round.
- A harness subagent comes before a dispatched session. Only the subagent reports completion without an explicit return path.
- Immutable reviews prefer native reporters. Mandatory dispatch added a second return path. Dispatch stays for enforced sandboxes, because a prompt does not enforce read-only access.
- Each launch selects one completion route. A waited result that is also read by hand gives one review two completions.

## Models and providers

- Model names and flags come from the CLI. They change between releases, and a wrong one kills the launch.
- A completed child task establishes delegation support. A catalog entry or a spawn does not.
- Provider rules live in `skills/references/model-providers.md`. A rule held only in `delegate` missed other coordinators.
- Model onboarding updates the reference, `dispatch.py` and Arc's standalone model table together. A copy left behind kept the old build.
- The `haiku` shorthand pins `claude-haiku-5-5`. Claude Code's alias selects Haiku 4.5 on some providers.

## Dispatch launchers

- Both launchers forward to `arc run`. Arc already owns the launch policy, the supervisor and the receipt, and the launchers' own copy of the read-only rules needed a test to stay equal to the shell copy.
- `dispatch.py` is the default launcher. `dispatch.sh` keeps the same flags, so a fallback drops none.
- A detached dispatch's log is read once after launch. A launch can die at once on a bad model name, and only its log shows why.
- `--prompt` takes `FILE` and rejects prompt text. A coordinator passed the text, and the error did not name the mistake.
- Every flag carries a one-line description. From names alone, a coordinator confused `--read-only` with `--full-access`, and `--attach` with `--wait`.
- A writable wait (`--wait-launch` or legacy `--wait`) prints the capped result text. Reading the result file costs another round trip.
- A read-only wait prints its result path, not the report text. A coordinator read a reviewer's report in its launch log before blinding, so the judge of that review was not blind. Read-only marks a review because every review launch is read-only. A read-only analysis that wants the text pays one more read.
- Both wait routes pass a worker exit code from 1 to 255 through. Arc's outcome alone gave every failed worker exit 1, so a caller could not branch on the worker's own code. The status read repeats up to 40 times, a quarter second apart, because Arc publishes the outcome before it saves the exit code. A worker exit 2 shares its code with the launcher's own failures, so the `worker exit:` line tells them apart.
- A waited launch that did not succeed prints Arc's observation reason on stderr. On Linux, a Codex launch with a missing binary printed a normal record with outcome `failed` and no cause.
- Each wait command stops after 25 hours without an outcome. Arc's supervisor publishes one within 24 hours of its start, and a supervisor that stopped without publishing left the wait running with no bound. `DISPATCH_WAIT_LIMIT_SECONDS` replaces the bound, so a test reaches it without waiting.
- A waited report without inline text prints its path and size, not a cause. Arc drops the text above its character cap and also above its JSON byte limit.
- Both wait routes follow `arc dispatch wait` past Arc's 110-second deadline. A review outlasts one deadline, and a waited launch that returned at it would leave the coordinator without a completion.
- An `arc run` reply in any state other than `starting`, `observed` or `deadline` fails the launch with exit 2. Arc returns `accepted-with-errors` with a launch ID when its supervisor never spawns, and a launcher that read only the launch ID printed a normal record and exited 0.
- `--read-tool` outside Claude `--read-only` refuses before the window route. `arc start` takes no read tools, so a reader-owned writable request dropped the flag silently.
- Attended Codex drops only the generated log path. An explicit `--log` reaches Arc, which refuses it. Dropping the explicit path lost a log the caller asked for, with no notice.
- A writable Claude launch into a folder that Claude has not trusted ends `failed`, with Claude's refusal as Arc's reason. Trust does not pass from a parent folder. Claude refused new scratch folders inside a trusted checkout, so a new worktree there fails the same way. Codex refuses an untrusted folder outside a Git repository the same way. Neither launcher accepts trust or passes `--skip-git-repo-check`, because trust lets the provider load that folder's settings, hooks and MCP servers.
- Dispatch refuses `--worktree` and `-w`. The calling workflow creates the tree at its base commit. A Claude `-w` tree started at `origin/HEAD`, and sessions started without the feature they had to design.
- A retry reuses its worktree through `--cwd`. The retry keeps the branch and path.
- Session names put job and round before the topic. Narrow rows then tell a review from a re-review.
- The shared skills folder is found by text, not by `..`. Installed skill junctions point at different repositories.

## Read-only Claude reviewers

- A read-only Claude reviewer runs Bash under auto mode. `dontAsk` denied chained and cross-directory reads, and one reviewer then read the working tree instead of the commit.
- The edit tools stay denied. The classifier can approve a write.
- Git write commands are denied, but that is no boundary. Another spelling reaches the classifier, so use Codex `--read-only` when no file must change.
- A model without auto mode runs in `dontAsk`. Manual mode silently lost every chained read.
- A `--read-tool` launch stays in `dontAsk`. Its settings carry allow rules that skip the classifier, and auto mode sends unnamed MCP operations to the classifier.

## Arc sessions and receipts

- Both launchers refuse a new session without `--arc-dispatch` before contacting Arc. A missing flag loses the portable parent link.
- The marked launch runs in the foreground with an explicit `--log PATH` before a separate background `--wait-launch LAUNCH_ID`. A host can return empty output for a background launch, leaving its marker outside the paired command result.
- An explicit log path selects the standalone route for foreground waited launches, including reader-owned writable requests with a ready Arc window. Ordinary owned requests without standalone-only flags retain their window route. Hosted manual launches do not support later waited observation.
- The printed `await:` command uses Bash quoting. On Windows, Python also prints a PowerShell command with its call operator and literal argument quoting. Use the form for the host shell.
- The observation deadline stays fixed across requests. Each Python subprocess retains the existing bounded startup and reply allowance so a final response can arrive after Arc ends its interval.
- `--arc-dispatch` goes in the parent Bash command. Arc reads only that command's output, and the wrapper's filename can change.
- A detached read-only print run chooses its session ID before launch. The launcher returns before Claude writes its JSON.
- `--owned` marks only a session the author requested. An owned consultation appeared under Today, and no later receipt retracts ownership.
- The launchers print Arc's `ARC_DISPATCH_SESSION=` marker unchanged and parse no native receipt. Arc reads the native background line, and a second parser could disagree with Arc's.
- The marker prints after a failed `arc dispatch wait` or `arc dispatch result`. The worker still runs, and without the marker the caller loses its link.
- Waited output frames receipt-shaped worker lines. Worker prose otherwise creates a false launch link.
- Windows Arc dispatch runs the JavaScript entry through Node. Batch wrappers mangle command characters and environment variables.
- A reader-owned writable dispatch prefers a ready Arc window. The reader continues in Arc's prompt box with no terminal writer lock.
- Only an explicit refusal from Arc permits a standalone launch. Any other outcome risks a second worker.

## Dispatch completion

- Arc delivers no callback into the parent session. Arc refuses a standalone callback because it cannot prove the original parent on the daemon, and the hosted parent notices were dropped unfinished.
- `--notify-thread` refuses before launch. A dropped flag would start a worker whose result nobody collects.
- Observation with `--wait-launch LAUNCH_ID` starts no worker. Pending identity and repeated deadlines preserve one launch and one overall wait deadline. The supervisor runs without a waiter.
- A review observation carries `--read-only` from its launch. Arc status omits the launch policy, so the printed observation command preserves the flag that keeps reports blinded.
- The completion routes for any worker are observation with `--wait-launch` and manual reads through `arc dispatch status` and `result`. Each one works without a callback.
- Claude `SendMessage` with `notify_when_idle` is a completion route only for a writable Claude worker under a Claude parent. The launching session's `ListAgents` lists that worker as a `bg` peer, which `SendMessage` needs as its address. Every other worker and parent uses separate observation, because no other pairing delivers the idle notice.
- Both wait routes return on an outcome that Arc publishes while the worker still runs. A wait that held until worker exit would block its caller on a worker that waits for an answer.

## Artifact settlement

- Owned disposition inventories catch aggregate small logs and ignored outputs that per-file limits miss. A digest-bound view prevents competing states while explicit migration preserves unrelated manual rows.

- Completed children settle independently of active parents and unresolved review findings. Named readers and verified archives preserve required evidence without granting deletion authority.

- Standalone resource views let plan-free runs check the disposition contract. Saving checked resource state under the plan branch rules prevents uncommitted closure edits without changing delivery authority.

## Sources

- [Claude Code model configuration](https://code.claude.com/docs/en/model-config) owns the provider alias defaults.
- [Claude Code permission modes](https://code.claude.com/docs/en/permission-modes) owns the model and CLI requirements for auto mode.

