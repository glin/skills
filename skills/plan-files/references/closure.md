# Disposition closure

Per-file limits miss aggregate small logs and ignored outputs. An adopted manifest accounts for every owned output. Its generated Runtime resources view prevents competing state claims. Explicit migration preserves legacy authority and unrelated manual rows.

Resolve the shared skills directory through the installed catalog. Set `CLOSURE_SHARED_ROOT` to that directory. The default is the installed `~/.agents/skills` directory. Read its `references/artifact-closure.md` for the manifest schema, inventory, migration, and view commands. Never resolve shared helpers by walking `..` through a skill junction. A missing checker is an unmet prerequisite.

The local adapter calls the single shared checker. It requires explicit adoption and checks the manifest against current files and the generated plan view. Keep control files outside inventoried output roots. Use exact owned run roots and scratch registrations. Never sweep other open plans. Completed children use explicit owner scopes, including nested child roots. Retained resources name a specific consumer, evidence, next owner, and release condition. Preserve named readers, verified archives, recovery copies, and blocked deletions.

```bash
python3 <plan-files>/scripts/check_closure.py migrate --manifest <absolute-manifest> --plan <absolute-plan> --owned-row <absolute-owned-legacy-path>
python3 <plan-files>/scripts/large-files.py --manifest <absolute-manifest> --plan <absolute-plan> --owner <completed-child>
```

For a run without a plan, save the shared checker's `view` output outside inventoried roots. Pass `--resource-view <absolute-view>` instead of `--plan` through either local adapter.

Apply migration output through a normal owned file edit. Preserve unrelated manual rows. Apply only the matching generated block when refreshing its digest. The checker never deletes, moves, changes permissions, or bypasses locks. Existing cleanup authority still governs correction. Positional large-file arguments and exit behavior remain available for legacy plans. Exit 0 passes, exit 1 reports unsettled outputs, and exit 2 reports an input or prerequisite error.
